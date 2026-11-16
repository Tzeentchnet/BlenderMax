# SPDX-FileCopyrightText: 2026 BlenderMax contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

import bpy
import numpy as np
from bpy_extras.anim_utils import action_ensure_channelbag_for_slot
from mathutils import Matrix, Quaternion, Vector


TICKS_PER_SECOND = 4800.0

PRS_CONTROL = 0x0000000000002005
LOOK_AT_CONTROL = 0x0000000000002006
POSITION_LIST = 0x000000004B4B1002
ROTATION_LIST = 0x000000004B4B1003
POSITION_WIRE = 0xFFEE238A118F7E02
ROTATION_WIRE = 0x3A90416731381913
SCALE_WIRE = 0xFEEE238B118F7C01

LINEAR_POSITION_KEYS = 0x2513
LINEAR_ROTATION_KEYS = 0x2514
LINEAR_SCALE_KEYS = 0x2515
TCB_POSITION_KEYS = 0x2521
TCB_ROTATION_KEYS = 0x2522
TCB_SCALE_KEYS = 0x2523
LEGACY_BEZIER_POSITION_KEYS = 0x2524
BEZIER_POSITION_KEYS = 0x2526
BEZIER_SCALE_KEYS = 0x2528
LEGACY_BEZIER_SCALE_KEYS = 0x2519


def _key_dtype(itemsize: int, components: int) -> np.dtype:
    return np.dtype(
        {
            "names": ("time", "flags", "value"),
            "formats": ("<i4", "<u4", ("<f4", components)),
            "offsets": (0, 4, 8),
            "itemsize": itemsize,
        }
    )


_POSITION_KEY_FORMATS = (
    (LINEAR_POSITION_KEYS, _key_dtype(20, 3)),
    (TCB_POSITION_KEYS, _key_dtype(64, 3)),
    (LEGACY_BEZIER_POSITION_KEYS, _key_dtype(56, 3)),
    (BEZIER_POSITION_KEYS, _key_dtype(80, 3)),
)
_ROTATION_KEY_FORMATS = (
    (LINEAR_ROTATION_KEYS, _key_dtype(24, 4)),
    (TCB_ROTATION_KEYS, _key_dtype(92, 4)),
)
_SCALE_KEY_FORMATS = (
    (LINEAR_SCALE_KEYS, _key_dtype(36, 7)),
    (TCB_SCALE_KEYS, _key_dtype(112, 7)),
    (LEGACY_BEZIER_SCALE_KEYS, _key_dtype(92, 7)),
    (BEZIER_SCALE_KEYS, _key_dtype(148, 7)),
)


def max_quaternion(values: Iterable[float]) -> Quaternion:
    x, y, z, w = values
    quaternion = Quaternion((w, -x, -y, -z))
    if sum(component * component for component in quaternion) <= np.finfo(np.float32).eps:
        return Quaternion()
    quaternion.normalize()
    return quaternion


def max_scale_matrix(values: Iterable[float]) -> Matrix:
    values = tuple(values)
    if len(values) < 3:
        return Matrix.Identity(4)
    scale = Matrix.Diagonal(Vector((*values[:3], 1.0)))
    if len(values) < 7:
        return scale

    x, y, z, w = values[3:7]
    axis = Quaternion((w, x, y, z))
    if sum(component * component for component in axis) <= np.finfo(np.float32).eps:
        return scale
    axis.normalize()
    axis_matrix = axis.to_matrix().to_4x4()
    return axis_matrix.transposed() @ scale @ axis_matrix


@dataclass(frozen=True)
class KeyTrack:
    times: np.ndarray
    values: np.ndarray

    @property
    def animated(self) -> bool:
        return len(self.times) > 1

    def evaluate_vectors(self, times: np.ndarray) -> np.ndarray:
        return np.column_stack(
            [np.interp(times, self.times, self.values[:, axis]) for axis in range(self.values.shape[1])]
        )

    def evaluate_quaternions(self, times: np.ndarray) -> list[Quaternion]:
        keys = [Quaternion(value) for value in self.values]
        result = []
        for time in times:
            upper = int(np.searchsorted(self.times, time, side="right"))
            if upper <= 0:
                result.append(keys[0].copy())
                continue
            if upper >= len(self.times):
                result.append(keys[-1].copy())
                continue
            lower = upper - 1
            span = self.times[upper] - self.times[lower]
            factor = 0.0 if span == 0.0 else float((time - self.times[lower]) / span)
            result.append(keys[lower].slerp(keys[upper], factor))
        return result


@dataclass(frozen=True)
class TransformTracks:
    position: KeyTrack | None = None
    rotation: KeyTrack | None = None
    scale: KeyTrack | None = None

    @property
    def animated(self) -> bool:
        return any(track and track.animated for track in (self.position, self.rotation, self.scale))

    def key_times(self) -> np.ndarray:
        tracks = [track.times for track in (self.position, self.rotation, self.scale) if track and track.animated]
        return np.unique(np.concatenate(tracks)) if tracks else np.empty(0, dtype=np.float64)


@dataclass(frozen=True)
class AnimationSamples:
    frames: np.ndarray
    locations: np.ndarray
    rotations: np.ndarray
    scales: np.ndarray


def _read_track(chunk, dtype: np.dtype, label: str) -> KeyTrack | None:
    if chunk is None:
        return None
    if not isinstance(chunk.data, bytes):
        raise ValueError(f"{label} key data is not a byte array")
    if len(chunk.data) % dtype.itemsize:
        raise ValueError(
            f"{label} key data has {len(chunk.data)} bytes; expected a multiple of {dtype.itemsize}"
        )
    if not chunk.data:
        return None

    records = np.frombuffer(chunk.data, dtype=dtype)
    times = records["time"].astype(np.float64)
    values = records["value"].astype(np.float64)
    order = np.argsort(times, kind="stable")
    times = times[order]
    values = values[order]

    if len(times) > 1:
        keep = np.r_[times[1:] != times[:-1], True]
        times = times[keep]
        values = values[keep]

    return KeyTrack(times=times, values=values)


def _read_controller_track(controller, formats, label: str) -> KeyTrack | None:
    if controller is None:
        return None
    for chunk_type, dtype in formats:
        chunk = controller.get_first(chunk_type)
        if chunk is not None:
            return _read_track(chunk, dtype, label)
    return None


def _first_reference(controller, references: Callable) -> object | None:
    refs = references(controller)
    return refs[0] if refs else None


def _unwrap_controller(controller, expected: str, guid: Callable, references: Callable):
    seen = set()
    while controller is not None and id(controller) not in seen:
        seen.add(id(controller))
        uid = guid(controller)
        if expected == "position" and uid in {POSITION_LIST, POSITION_WIRE}:
            controller = _first_reference(controller, references)
        elif expected == "rotation" and uid in {ROTATION_LIST, ROTATION_WIRE}:
            controller = _first_reference(controller, references)
        elif expected == "scale" and uid == SCALE_WIRE:
            controller = _first_reference(controller, references)
        else:
            break
    return controller


def extract_transform_tracks(
    prs,
    guid: Callable,
    references: Callable,
) -> TransformTracks:
    if prs is None:
        return TransformTracks()

    controllers = references(prs)
    uid = guid(prs)
    if uid == PRS_CONTROL:
        position_index, rotation_index, scale_index = 0, 1, 2
    elif uid == LOOK_AT_CONTROL:
        position_index, rotation_index, scale_index = 1, None, 3
    else:
        return TransformTracks()

    position = _unwrap_controller(
        controllers[position_index] if len(controllers) > position_index else None,
        "position",
        guid,
        references,
    )
    rotation = _unwrap_controller(
        controllers[rotation_index]
        if rotation_index is not None and len(controllers) > rotation_index
        else None,
        "rotation",
        guid,
        references,
    )
    scale = _unwrap_controller(
        controllers[scale_index] if len(controllers) > scale_index else None,
        "scale",
        guid,
        references,
    )

    position_track = _read_controller_track(position, _POSITION_KEY_FORMATS, "position")
    rotation_track = _read_controller_track(rotation, _ROTATION_KEY_FORMATS, "rotation")
    scale_track = _read_controller_track(scale, _SCALE_KEY_FORMATS, "scale")

    if rotation_track is not None:
        source = rotation_track.values
        converted = np.column_stack((source[:, 3], -source[:, :3]))
        lengths = np.linalg.norm(converted, axis=1)
        valid = lengths > np.finfo(np.float64).eps
        converted[valid] /= lengths[valid, None]
        converted[~valid] = (1.0, 0.0, 0.0, 0.0)
        rotation_track = KeyTrack(rotation_track.times, converted)

    return TransformTracks(
        position=position_track,
        rotation=rotation_track,
        scale=scale_track,
    )


def _sample_times(tracks: TransformTracks, ticks_per_frame: float, sampling: str) -> np.ndarray:
    key_times = tracks.key_times()
    if not len(key_times):
        return key_times
    if sampling == "KEYS":
        return key_times

    first_frame = np.floor(key_times[0] / ticks_per_frame)
    last_frame = np.ceil(key_times[-1] / ticks_per_frame)
    return np.arange(first_frame, last_frame + 1.0, dtype=np.float64) * ticks_per_frame


def sample_transform(
    tracks: TransformTracks,
    target_anchor: Matrix,
    source_anchor: Matrix,
    fps: float,
    frame_offset: float,
    sampling: str,
) -> AnimationSamples | None:
    ticks_per_frame = TICKS_PER_SECOND / fps
    times = _sample_times(tracks, ticks_per_frame, sampling)
    if not len(times):
        return None

    default_location, default_rotation, default_scale = source_anchor.decompose()
    locations = (
        tracks.position.evaluate_vectors(times)
        if tracks.position
        else np.tile(default_location, (len(times), 1))
    )
    rotations = (
        tracks.rotation.evaluate_quaternions(times)
        if tracks.rotation
        else [default_rotation.copy() for _ in times]
    )
    scale_values = (
        tracks.scale.evaluate_vectors(times)
        if tracks.scale
        else np.tile((*default_scale, 0.0, 0.0, 0.0, 1.0), (len(times), 1))
    )

    anchor_time = float(np.clip(0.0, times[0], times[-1]))
    anchor_location = (
        Vector(tracks.position.evaluate_vectors(np.array([anchor_time]))[0])
        if tracks.position
        else default_location
    )
    anchor_rotation = (
        tracks.rotation.evaluate_quaternions(np.array([anchor_time]))[0]
        if tracks.rotation
        else default_rotation
    )
    anchor_scale_value = (
        tracks.scale.evaluate_vectors(np.array([anchor_time]))[0]
        if tracks.scale
        else (*default_scale, 0.0, 0.0, 0.0, 1.0)
    )
    sampled_anchor = (
        Matrix.Translation(anchor_location)
        @ anchor_rotation.to_matrix().to_4x4()
        @ max_scale_matrix(anchor_scale_value)
    )
    sampled_anchor_inverse = sampled_anchor.inverted_safe()

    output_locations = np.empty((len(times), 3), dtype=np.float32)
    output_rotations = np.empty((len(times), 4), dtype=np.float32)
    output_scales = np.empty((len(times), 3), dtype=np.float32)
    previous_rotation = None

    for index, (location, rotation, scale_value) in enumerate(
        zip(locations, rotations, scale_values)
    ):
        source_matrix = (
            Matrix.Translation(Vector(location))
            @ rotation.to_matrix().to_4x4()
            @ max_scale_matrix(scale_value)
        )
        matrix = target_anchor @ sampled_anchor_inverse @ source_matrix
        target_location, target_rotation, target_scale = matrix.decompose()
        if previous_rotation is not None and previous_rotation.dot(target_rotation) < 0.0:
            target_rotation.negate()
        previous_rotation = target_rotation.copy()
        output_locations[index] = target_location
        output_rotations[index] = target_rotation
        output_scales[index] = target_scale

    return AnimationSamples(
        frames=times / ticks_per_frame + frame_offset,
        locations=output_locations,
        rotations=output_rotations,
        scales=output_scales,
    )


class ActionBuilder:
    def __init__(self, name: str):
        self.action = bpy.data.actions.new(name=name)
        self.animated_objects = 0
        self.frame_start = None
        self.frame_end = None

    def add_object(self, obj: bpy.types.Object, samples: AnimationSamples) -> None:
        obj.rotation_mode = "QUATERNION"
        slot = self.action.slots.new(id_type="OBJECT", name=obj.name)
        obj.animation_data_create()
        obj.animation_data.action = self.action
        obj.animation_data.action_slot = slot
        channelbag = action_ensure_channelbag_for_slot(self.action, slot)

        self._add_channels(channelbag, "location", samples.frames, samples.locations)
        self._add_channels(
            channelbag,
            "rotation_quaternion",
            samples.frames,
            samples.rotations,
        )
        self._add_channels(channelbag, "scale", samples.frames, samples.scales)

        sample_start = float(samples.frames[0])
        sample_end = float(samples.frames[-1])
        self.frame_start = sample_start if self.frame_start is None else min(self.frame_start, sample_start)
        self.frame_end = sample_end if self.frame_end is None else max(self.frame_end, sample_end)
        self.animated_objects += 1

    @staticmethod
    def _add_channels(channelbag, data_path: str, frames: np.ndarray, values: np.ndarray) -> None:
        for axis in range(values.shape[1]):
            fcurve = channelbag.fcurves.ensure(
                data_path=data_path,
                index=axis,
                group_name="Transform",
            )
            points = fcurve.keyframe_points
            points.add(len(frames))
            coordinates = np.column_stack((frames, values[:, axis])).astype(np.float32, copy=False)
            points.foreach_set("co", coordinates.ravel())
            for point in points:
                point.interpolation = "LINEAR"
            fcurve.update()

    def finish(self) -> bpy.types.Action | None:
        if self.animated_objects:
            return self.action
        bpy.data.actions.remove(self.action)
        return None
