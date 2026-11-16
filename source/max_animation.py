# SPDX-FileCopyrightText: 2026 BlenderMax contributors
#
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Iterable, Literal

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

Interpolation = Literal["LINEAR", "BEZIER", "TCB"]


@dataclass(frozen=True)
class KeyFormat:
    chunk_type: int
    dtype: np.dtype
    interpolation: Interpolation
    tangent_time_scale: float = 1.0


def _key_dtype(
    itemsize: int,
    components: int,
    **fields: tuple[int, int],
) -> np.dtype:
    names = ["time", "flags", "value"]
    formats = ["<i4", "<u4", ("<f4", components)]
    offsets = [0, 4, 8]
    for name, (offset, field_components) in fields.items():
        names.append(name)
        formats.append("<f4" if field_components == 1 else ("<f4", field_components))
        offsets.append(offset)
    return np.dtype(
        {
            "names": names,
            "formats": formats,
            "offsets": offsets,
            "itemsize": itemsize,
        }
    )


_POSITION_KEY_FORMATS = (
    KeyFormat(LINEAR_POSITION_KEYS, _key_dtype(20, 3), "LINEAR"),
    KeyFormat(TCB_POSITION_KEYS, _key_dtype(64, 3, tcb=(20, 5)), "TCB"),
    KeyFormat(
        LEGACY_BEZIER_POSITION_KEYS,
        _key_dtype(56, 3, in_tangent=(20, 3), out_tangent=(32, 3)),
        "BEZIER",
    ),
    KeyFormat(
        BEZIER_POSITION_KEYS,
        _key_dtype(80, 3, in_tangent=(20, 3), out_tangent=(32, 3)),
        "BEZIER",
    ),
)
_ROTATION_KEY_FORMATS = (
    KeyFormat(LINEAR_ROTATION_KEYS, _key_dtype(24, 4), "LINEAR"),
    KeyFormat(TCB_ROTATION_KEYS, _key_dtype(92, 4, tcb=(24, 5)), "TCB"),
)
_SCALE_KEY_FORMATS = (
    KeyFormat(LINEAR_SCALE_KEYS, _key_dtype(36, 7), "LINEAR"),
    KeyFormat(TCB_SCALE_KEYS, _key_dtype(112, 7, tcb=(36, 5)), "TCB"),
    KeyFormat(
        LEGACY_BEZIER_SCALE_KEYS,
        _key_dtype(92, 7, in_tangent=(36, 3), out_tangent=(64, 3)),
        "BEZIER",
        1.0 / TICKS_PER_SECOND,
    ),
    KeyFormat(
        BEZIER_SCALE_KEYS,
        _key_dtype(148, 7, in_tangent=(36, 3), out_tangent=(64, 3)),
        "BEZIER",
        1.0 / TICKS_PER_SECOND,
    ),
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


def _segment_parameters(
    key_times: np.ndarray,
    sample_times: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    upper = np.searchsorted(key_times, sample_times, side="right")
    lower = np.clip(upper - 1, 0, len(key_times) - 1)
    upper = np.clip(upper, 0, len(key_times) - 1)
    spans = key_times[upper] - key_times[lower]
    factors = np.divide(
        sample_times - key_times[lower],
        spans,
        out=np.zeros_like(sample_times, dtype=np.float64),
        where=spans != 0.0,
    )
    return lower, upper, spans, np.clip(factors, 0.0, 1.0)


def _tcb_factors(
    parameters: np.ndarray,
    times: np.ndarray,
    index: int,
) -> tuple[float, float, float, float]:
    tension, continuity, bias = parameters[index, :3]
    if index == 0 or index == len(times) - 1:
        previous_time_factor = next_time_factor = 1.0
    else:
        half_span = 0.5 * (times[index + 1] - times[index - 1])
        previous_time_factor = (times[index] - times[index - 1]) / half_span
        next_time_factor = (times[index + 1] - times[index]) / half_span
        continuity_magnitude = abs(continuity)
        previous_time_factor += continuity_magnitude * (1.0 - previous_time_factor)
        next_time_factor += continuity_magnitude * (1.0 - next_time_factor)

    half_tension = 0.5 * (1.0 - tension)
    continuity_minus = 1.0 - continuity
    continuity_plus = 1.0 + continuity
    bias_minus = 1.0 - bias
    bias_plus = 1.0 + bias
    return (
        half_tension * continuity_minus * bias_plus * previous_time_factor,
        half_tension * continuity_plus * bias_minus * previous_time_factor,
        half_tension * continuity_plus * bias_plus * next_time_factor,
        half_tension * continuity_minus * bias_minus * next_time_factor,
    )


def _tcb_vector_tangents(
    times: np.ndarray,
    values: np.ndarray,
    parameters: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    incoming = np.zeros_like(values)
    outgoing = np.zeros_like(values)
    if len(times) <= 1:
        return incoming, outgoing
    if len(times) == 2:
        delta = values[1] - values[0]
        outgoing[0] = delta * (1.0 - parameters[0, 0])
        incoming[1] = delta * (1.0 - parameters[1, 0])
        return incoming, outgoing

    for index in range(1, len(times) - 1):
        incoming_previous, incoming_next, outgoing_previous, outgoing_next = _tcb_factors(
            parameters,
            times,
            index,
        )
        previous_delta = values[index] - values[index - 1]
        next_delta = values[index + 1] - values[index]
        incoming[index] = (
            previous_delta * incoming_previous + next_delta * incoming_next
        )
        outgoing[index] = (
            previous_delta * outgoing_previous + next_delta * outgoing_next
        )

    outgoing[0] = 0.5 * (1.0 - parameters[0, 0]) * (
        3.0 * (values[1] - values[0]) - incoming[1]
    )
    incoming[-1] = 0.5 * (1.0 - parameters[-1, 0]) * (
        3.0 * (values[-1] - values[-2]) - outgoing[-2]
    )
    return incoming, outgoing


def _tcb_ease(
    factors: np.ndarray,
    ease_out: np.ndarray,
    ease_in: np.ndarray,
) -> np.ndarray:
    ease_out = np.asarray(ease_out, dtype=np.float64).copy()
    ease_in = np.asarray(ease_in, dtype=np.float64).copy()
    ease_sum = ease_out + ease_in
    normalize = ease_sum > 1.0
    ease_out[normalize] /= ease_sum[normalize]
    ease_in[normalize] /= ease_sum[normalize]

    eased = factors.copy()
    active = (factors > 0.0) & (factors < 1.0) & ((ease_out + ease_in) != 0.0)
    scale = 1.0 / (2.0 - ease_out - ease_in)
    ease_start = active & (ease_out != 0.0) & (factors < ease_out)
    ease_middle = active & ~ease_start & (factors < 1.0 - ease_in)
    ease_end = active & ~ease_start & ~ease_middle & (ease_in != 0.0)
    eased[ease_start] = (
        scale[ease_start] / ease_out[ease_start] * factors[ease_start] ** 2
    )
    eased[ease_middle] = scale[ease_middle] * (
        2.0 * factors[ease_middle] - ease_out[ease_middle]
    )
    remaining = 1.0 - factors[ease_end]
    eased[ease_end] = 1.0 - scale[ease_end] / ease_in[ease_end] * remaining**2
    return eased


def _normalize_quaternion(value: np.ndarray) -> np.ndarray:
    length = np.linalg.norm(value)
    if length <= np.finfo(np.float64).eps:
        return np.asarray((1.0, 0.0, 0.0, 0.0), dtype=np.float64)
    return value / length


def _multiply_quaternions(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return np.asarray(
        (
            lw * rw - lx * rx - ly * ry - lz * rz,
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
        ),
        dtype=np.float64,
    )


def _quaternion_log_difference(start: np.ndarray, end: np.ndarray) -> np.ndarray:
    difference = _multiply_quaternions(
        np.asarray((start[0], -start[1], -start[2], -start[3])),
        end,
    )
    difference = _normalize_quaternion(difference)
    vector_length = np.linalg.norm(difference[1:])
    if vector_length <= np.finfo(np.float64).eps:
        return np.zeros(3, dtype=np.float64)
    return difference[1:] * (np.arctan2(vector_length, difference[0]) / vector_length)


def _quaternion_exp(vector: np.ndarray) -> np.ndarray:
    angle = np.linalg.norm(vector)
    if angle <= np.finfo(np.float64).eps:
        return np.asarray((1.0, 0.0, 0.0, 0.0), dtype=np.float64)
    return np.asarray(
        (np.cos(angle), *(vector * (np.sin(angle) / angle))),
        dtype=np.float64,
    )


def _quaternion_slerp(start: np.ndarray, end: np.ndarray, factor: float) -> np.ndarray:
    start = _normalize_quaternion(start)
    end = _normalize_quaternion(end)
    cosine = float(np.dot(start, end))
    if cosine < 0.0:
        end = -end
        cosine = -cosine
    cosine = float(np.clip(cosine, -1.0, 1.0))
    if cosine > 1.0 - 1e-8:
        return _normalize_quaternion(start + factor * (end - start))
    angle = np.arccos(cosine)
    sine = np.sin(angle)
    return _normalize_quaternion(
        start * (np.sin((1.0 - factor) * angle) / sine)
        + end * (np.sin(factor * angle) / sine)
    )


def _quaternion_squad(
    start: np.ndarray,
    start_control: np.ndarray,
    end_control: np.ndarray,
    end: np.ndarray,
    factor: float,
) -> np.ndarray:
    endpoints = _quaternion_slerp(start, end, factor)
    controls = _quaternion_slerp(start_control, end_control, factor)
    return _quaternion_slerp(endpoints, controls, 2.0 * factor * (1.0 - factor))


def _tcb_quaternion_controls(
    times: np.ndarray,
    values: np.ndarray,
    parameters: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    quaternions = np.asarray(
        [_normalize_quaternion(value) for value in values],
        dtype=np.float64,
    )
    for index in range(1, len(quaternions)):
        if np.dot(quaternions[index - 1], quaternions[index]) < 0.0:
            quaternions[index] *= -1.0

    incoming = quaternions.copy()
    outgoing = quaternions.copy()
    if len(quaternions) <= 1:
        return quaternions, incoming, outgoing

    for index, quaternion in enumerate(quaternions):
        previous_log = (
            _quaternion_log_difference(quaternions[index - 1], quaternion)
            if index > 0
            else None
        )
        next_log = (
            _quaternion_log_difference(quaternion, quaternions[index + 1])
            if index + 1 < len(quaternions)
            else None
        )
        if previous_log is None:
            previous_log = next_log
        if next_log is None:
            next_log = previous_log

        incoming_previous, incoming_next, outgoing_previous, outgoing_next = _tcb_factors(
            parameters,
            times,
            index,
        )
        incoming_delta = 0.5 * (
            previous_log * (1.0 - incoming_previous) - next_log * incoming_next
        )
        outgoing_delta = 0.5 * (
            previous_log * outgoing_previous + next_log * (outgoing_next - 1.0)
        )
        incoming[index] = _normalize_quaternion(
            _multiply_quaternions(quaternion, _quaternion_exp(incoming_delta))
        )
        outgoing[index] = _normalize_quaternion(
            _multiply_quaternions(quaternion, _quaternion_exp(outgoing_delta))
        )
    return quaternions, incoming, outgoing


@dataclass(frozen=True)
class KeyTrack:
    times: np.ndarray
    values: np.ndarray
    interpolation: Interpolation = "LINEAR"
    flags: np.ndarray | None = None
    in_tangents: np.ndarray | None = None
    out_tangents: np.ndarray | None = None
    tcb: np.ndarray | None = None
    tangent_time_scale: float = 1.0

    @property
    def animated(self) -> bool:
        return len(self.times) > 1

    def evaluate_vectors(self, times: np.ndarray) -> np.ndarray:
        times = np.asarray(times, dtype=np.float64)
        if (
            self.interpolation == "BEZIER"
            and self.in_tangents is not None
            and self.out_tangents is not None
        ):
            lower, upper, spans, factors = _segment_parameters(self.times, times)
            result = (
                self.values[lower] * (1.0 - factors[:, None])
                + self.values[upper] * factors[:, None]
            )
            component_count = self.in_tangents.shape[1]
            start = self.values[lower, :component_count]
            end = self.values[upper, :component_count]
            start_control = (
                start
                + self.out_tangents[lower]
                * (spans * self.tangent_time_scale)[:, None]
                / 3.0
            )
            end_control = (
                end
                + self.in_tangents[upper]
                * (spans * self.tangent_time_scale)[:, None]
                / 3.0
            )
            inverse = 1.0 - factors
            result[:, :component_count] = (
                start * inverse[:, None] ** 3
                + 3.0 * start_control * (inverse**2 * factors)[:, None]
                + 3.0 * end_control * (inverse * factors**2)[:, None]
                + end * factors[:, None] ** 3
            )
            if self.flags is not None:
                step = ((self.flags[lower] >> 10) & 0x7) == 2
                result[step] = self.values[lower[step]]
            return result

        if self.interpolation == "TCB" and self.tcb is not None:
            lower, upper, _spans, factors = _segment_parameters(self.times, times)
            eased = _tcb_ease(
                factors,
                self.tcb[lower, 4],
                self.tcb[upper, 3],
            )
            result = (
                self.values[lower] * (1.0 - eased[:, None])
                + self.values[upper] * eased[:, None]
            )
            component_count = min(3, self.values.shape[1])
            incoming, outgoing = _tcb_vector_tangents(
                self.times,
                self.values[:, :component_count],
                self.tcb,
            )
            squared = eased * eased
            cubed = squared * eased
            blend = 3.0 * squared - 2.0 * cubed
            result[:, :component_count] = (
                self.values[lower, :component_count] * (1.0 - blend)[:, None]
                + self.values[upper, :component_count] * blend[:, None]
                + outgoing[lower] * (cubed - 2.0 * squared + eased)[:, None]
                + incoming[upper] * (cubed - squared)[:, None]
            )
            return result

        return np.column_stack(
            [np.interp(times, self.times, self.values[:, axis]) for axis in range(self.values.shape[1])]
        )

    def evaluate_quaternions(self, times: np.ndarray) -> list[Quaternion]:
        if self.interpolation == "TCB" and self.tcb is not None:
            quaternions, incoming, outgoing = _tcb_quaternion_controls(
                self.times,
                self.values,
                self.tcb,
            )
            lower, upper, _spans, factors = _segment_parameters(
                self.times,
                np.asarray(times, dtype=np.float64),
            )
            eased = _tcb_ease(
                factors,
                self.tcb[lower, 4],
                self.tcb[upper, 3],
            )
            return [
                Quaternion(
                    _quaternion_squad(
                        quaternions[start],
                        outgoing[start],
                        incoming[end],
                        quaternions[end],
                        float(factor),
                    )
                )
                for start, end, factor in zip(lower, upper, eased)
            ]

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


def _read_track(chunk, key_format: KeyFormat, label: str) -> KeyTrack | None:
    if chunk is None:
        return None
    if not isinstance(chunk.data, bytes):
        raise ValueError(f"{label} key data is not a byte array")
    if len(chunk.data) % key_format.dtype.itemsize:
        raise ValueError(
            f"{label} key data has {len(chunk.data)} bytes; "
            f"expected a multiple of {key_format.dtype.itemsize}"
        )
    if not chunk.data:
        return None

    records = np.frombuffer(chunk.data, dtype=key_format.dtype)
    arrays = {
        "times": records["time"].astype(np.float64),
        "values": records["value"].astype(np.float64),
        "flags": records["flags"].astype(np.uint32),
        "in_tangents": (
            records["in_tangent"].astype(np.float64)
            if "in_tangent" in records.dtype.names
            else None
        ),
        "out_tangents": (
            records["out_tangent"].astype(np.float64)
            if "out_tangent" in records.dtype.names
            else None
        ),
        "tcb": (
            records["tcb"].astype(np.float64)
            if "tcb" in records.dtype.names
            else None
        ),
    }
    order = np.argsort(arrays["times"], kind="stable")
    arrays = {
        name: values[order] if values is not None else None
        for name, values in arrays.items()
    }

    if len(arrays["times"]) > 1:
        keep = np.r_[arrays["times"][1:] != arrays["times"][:-1], True]
        arrays = {
            name: values[keep] if values is not None else None
            for name, values in arrays.items()
        }

    return KeyTrack(
        **arrays,
        interpolation=key_format.interpolation,
        tangent_time_scale=key_format.tangent_time_scale,
    )


def _read_controller_track(controller, formats, label: str) -> KeyTrack | None:
    if controller is None:
        return None
    for key_format in formats:
        chunk = controller.get_first(key_format.chunk_type)
        if chunk is not None:
            return _read_track(chunk, key_format, label)
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
        rotation_track = replace(rotation_track, values=converted)

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
