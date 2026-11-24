# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import hashlib
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy
import numpy as np
from mathutils import Matrix


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from source import import_max  # noqa: E402
from source.import_max import (  # noqa: E402
    Mesh3d,
    apply_smoothing_groups,
    calc_point_3d,
    draw_shape,
    get_multi_material_ids,
    get_mesh_polys,
    remap_material_indices,
)
from source.max_animation import (  # noqa: E402
    ActionBuilder,
    TICKS_PER_SECOND,
    extract_transform_tracks,
    max_quaternion,
    max_scale_matrix,
    sample_transform,
)
from tools.blender_import import validate_scene_stats  # noqa: E402
from tools.inspect_max import (  # noqa: E402
    data_summary,
    derived_stack_summaries,
)


class Chunk:
    def __init__(self, guid: int, chunks=None, references=None, data=b""):
        self.guid = guid
        self.types = guid
        self.chunks = chunks or {}
        self.children = list(self.chunks.values())
        self.references = references or []
        self.data = data

    def get_first(self, chunk_type: int):
        return self.chunks.get(chunk_type)


def key_data(
    record_size: int,
    values: list[tuple[int, tuple[float, ...]]],
    *,
    flags: list[int] | None = None,
    fields: list[dict[int, tuple[float, ...]]] | None = None,
) -> bytes:
    data = bytearray(record_size * len(values))
    for index, (time, value) in enumerate(values):
        offset = index * record_size
        struct.pack_into("<iI", data, offset, time, flags[index] if flags else 0)
        struct.pack_into("<%df" % len(value), data, offset + 8, *value)
        if fields:
            for field_offset, field_value in fields[index].items():
                struct.pack_into(
                    "<%df" % len(field_value),
                    data,
                    offset + field_offset,
                    *field_value,
                )
    return bytes(data)


class AnimationTests(unittest.TestCase):
    def setUp(self):
        bpy.ops.wm.read_factory_settings(use_empty=True)

    def test_extracts_numpy_accelerated_prs_tracks(self):
        position = Chunk(
            0x2008,
            chunks={
                0x2524: Chunk(
                    0x2524,
                    data=key_data(
                        56,
                        [(0, (0.0, 0.0, 0.0)), (160, (1.0, 2.0, 3.0))],
                    ),
                )
            },
        )
        rotation = Chunk(
            0x442313,
            chunks={
                0x2522: Chunk(
                    0x2522,
                    data=key_data(
                        92,
                        [(0, (0.0, 0.0, 0.0, 1.0)), (160, (0.0, 0.0, 1.0, 0.0))],
                    ),
                )
            },
        )
        scale = Chunk(
            0x2010,
            chunks={
                0x2519: Chunk(
                    0x2519,
                    data=key_data(
                        92,
                        [(0, (1.0, 1.0, 1.0)), (160, (2.0, 2.0, 2.0))],
                    ),
                )
            },
        )
        prs = Chunk(0x2005, references=[position, rotation, scale])

        tracks = extract_transform_tracks(
            prs,
            lambda chunk: chunk.guid,
            lambda chunk: chunk.references,
        )

        self.assertTrue(tracks.animated)
        np.testing.assert_array_equal(tracks.position.times, (0.0, 160.0))
        np.testing.assert_allclose(tracks.position.values[1], (1.0, 2.0, 3.0))
        np.testing.assert_allclose(tracks.rotation.values[0], (1.0, 0.0, 0.0, 0.0))
        np.testing.assert_allclose(tracks.scale.values[1, :3], (2.0, 2.0, 2.0))

    def test_uvw_face_indices_with_and_without_count_header(self):
        faces = [[0, 1, 2], [2, 3, 0]]
        headerless = SimpleNamespace(data=np.asarray(faces, dtype="<u4").tobytes())
        headed = SimpleNamespace(
            data=np.asarray([2] + sum(faces, []), dtype="<u4").tobytes()
        )

        np.testing.assert_array_equal(
            import_max.get_uvw_coords(headerless, face_count=2), faces
        )
        np.testing.assert_array_equal(
            import_max.get_uvw_coords(headed, face_count=2), faces
        )
        np.testing.assert_array_equal(import_max.get_uvw_coords(headed), faces)

    def test_missing_ole_stream_raises_file_not_found(self):
        maxfile = object.__new__(import_max.ImportMaxFile)
        maxfile.root = SimpleNamespace(
            sid=0,
            name="Root Entry",
            kids=[SimpleNamespace(sid=1, name="Scene", kids=[])],
        )

        self.assertEqual(maxfile._find("scene"), 1)
        with self.assertRaisesRegex(FileNotFoundError, "ClassDirectory3"):
            maxfile._find("ClassDirectory3")

    def test_reads_legacy_class_directory_2(self):
        class_directory = Chunk(0)
        calls = []

        def fake_read_chunks(_maxfile, name, *_args, **_kwargs):
            calls.append(name)
            if name == "ClassDirectory3":
                raise FileNotFoundError(name)
            return [class_directory]

        previous = import_max.CLS_DIR3_LIST
        self.addCleanup(setattr, import_max, "CLS_DIR3_LIST", previous)
        with patch.object(import_max, "read_chunks", side_effect=fake_read_chunks):
            import_max.read_class_directory(object(), "legacy.max")

        self.assertEqual(calls, ["ClassDirectory3", "ClassDirectory2"])
        self.assertIs(import_max.CLS_DIR3_LIST[0], class_directory)

    def test_rejects_empty_corpus_imports(self):
        with self.assertRaisesRegex(ValueError, "empty scene"):
            validate_scene_stats(
                {
                    "finite_transforms": True,
                    "objects": 0,
                }
            )

    def test_extracts_supported_modern_key_layouts(self):
        cases = (
            ("position", 0x2002, 0x2513, 20, (1.0, 2.0, 3.0)),
            ("position", 0x442312, 0x2521, 64, (1.0, 2.0, 3.0)),
            ("position", 0x2008, 0x2526, 80, (1.0, 2.0, 3.0)),
            ("rotation", 0x2003, 0x2514, 24, (0.0, 0.0, 0.0, 1.0)),
            ("scale", 0x2004, 0x2515, 36, (1.0, 2.0, 3.0)),
            ("scale", 0x442315, 0x2523, 112, (1.0, 2.0, 3.0)),
            ("scale", 0x2010, 0x2528, 148, (1.0, 2.0, 3.0)),
        )
        for track_name, controller_guid, chunk_type, record_size, value in cases:
            with self.subTest(track=track_name, chunk=hex(chunk_type)):
                controller = Chunk(
                    controller_guid,
                    chunks={
                        chunk_type: Chunk(
                            chunk_type,
                            data=key_data(record_size, [(0, value), (160, value)]),
                        )
                    },
                )
                references = [None, None, None]
                references[{"position": 0, "rotation": 1, "scale": 2}[track_name]] = controller
                tracks = extract_transform_tracks(
                    Chunk(0x2005, references=references),
                    lambda chunk: chunk.guid,
                    lambda chunk: chunk.references,
                )
                track = getattr(tracks, track_name)
                self.assertIsNotNone(track)
                np.testing.assert_array_equal(track.times, (0.0, 160.0))
                expected = (1.0, 0.0, 0.0, 0.0) if track_name == "rotation" else value
                np.testing.assert_allclose(track.values[0, :len(expected)], expected)

    def test_evaluates_legacy_and_modern_bezier_tangents(self):
        cases = (
            ("position", 0x2008, 0x2524, 56, 20, 32, 3),
            ("position", 0x2008, 0x2526, 80, 20, 32, 3),
            ("scale", 0x2010, 0x2519, 92, 36, 64, 7),
            ("scale", 0x2010, 0x2528, 148, 36, 64, 7),
        )
        for track_name, controller_guid, chunk_type, record_size, in_offset, out_offset, components in cases:
            with self.subTest(track=track_name, chunk=hex(chunk_type)):
                start = (0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0)[:components]
                end = (1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0)[:components]
                tangent = 0.03 if track_name == "position" else 0.03 * TICKS_PER_SECOND
                data = key_data(
                    record_size,
                    [(0, start), (100, end)],
                    fields=[
                        {out_offset: (tangent, 0.0, 0.0)},
                        {in_offset: (-tangent, 0.0, 0.0)},
                    ],
                )
                controller = Chunk(
                    controller_guid,
                    chunks={chunk_type: Chunk(chunk_type, data=data)},
                )
                references = [None, None, None]
                references[0 if track_name == "position" else 2] = controller
                track = getattr(
                    extract_transform_tracks(
                        Chunk(0x2005, references=references),
                        lambda chunk: chunk.guid,
                        lambda chunk: chunk.references,
                    ),
                    track_name,
                )

                self.assertEqual(track.interpolation, "BEZIER")
                np.testing.assert_allclose(
                    track.evaluate_vectors(np.asarray((25.0,)))[:, 0],
                    (0.4375,),
                )

    def test_evaluates_bezier_step_tangent(self):
        position = Chunk(
            0x2008,
            chunks={
                0x2526: Chunk(
                    0x2526,
                    data=key_data(
                        80,
                        [(0, (0.0, 0.0, 0.0)), (100, (1.0, 0.0, 0.0))],
                        flags=[2 << 10, 0],
                    ),
                )
            },
        )
        track = extract_transform_tracks(
            Chunk(0x2005, references=[position, None, None]),
            lambda chunk: chunk.guid,
            lambda chunk: chunk.references,
        ).position

        np.testing.assert_allclose(
            track.evaluate_vectors(np.asarray((25.0, 99.0, 100.0)))[:, 0],
            (0.0, 0.0, 1.0),
        )

    def test_evaluates_tcb_tension_continuity_bias_and_ease(self):
        def position_track(
            values: list[tuple[int, tuple[float, ...]]],
            parameters: list[tuple[float, ...]],
        ):
            position = Chunk(
                0x442312,
                chunks={
                    0x2521: Chunk(
                        0x2521,
                        data=key_data(
                            64,
                            values,
                            fields=[{20: value} for value in parameters],
                        ),
                    )
                },
            )
            return extract_transform_tracks(
                Chunk(0x2005, references=[position, None, None]),
                lambda chunk: chunk.guid,
                lambda chunk: chunk.references,
            ).position

        tension = position_track(
            [(0, (0.0, 0.0, 0.0)), (100, (1.0, 0.0, 0.0))],
            [(1.0, 0.0, 0.0, 0.0, 0.0)] * 2,
        )
        continuity = position_track(
            [
                (0, (0.0, 0.0, 0.0)),
                (100, (1.0, 0.0, 0.0)),
                (200, (3.0, 0.0, 0.0)),
            ],
            [
                (0.0, 0.0, 0.0, 0.0, 0.0),
                (0.0, 1.0, 0.0, 0.0, 0.0),
                (0.0, 0.0, 0.0, 0.0, 0.0),
            ],
        )
        bias = position_track(
            [
                (0, (0.0, 0.0, 0.0)),
                (100, (1.0, 0.0, 0.0)),
                (200, (3.0, 0.0, 0.0)),
            ],
            [
                (0.0, 0.0, 0.0, 0.0, 0.0),
                (0.0, 0.0, 1.0, 0.0, 0.0),
                (0.0, 0.0, 0.0, 0.0, 0.0),
            ],
        )
        ease = position_track(
            [(0, (0.0, 0.0, 0.0)), (100, (1.0, 0.0, 0.0))],
            [
                (0.0, 0.0, 0.0, 0.0, 0.5),
                (0.0, 0.0, 0.0, 0.0, 0.0),
            ],
        )

        self.assertEqual(tension.interpolation, "TCB")
        np.testing.assert_allclose(
            tension.evaluate_vectors(np.asarray((25.0,)))[:, 0],
            (0.15625,),
        )
        np.testing.assert_allclose(
            continuity.evaluate_vectors(np.asarray((50.0,)))[:, 0],
            (0.3125,),
        )
        np.testing.assert_allclose(
            bias.evaluate_vectors(np.asarray((50.0,)))[:, 0],
            (0.5,),
        )
        np.testing.assert_allclose(
            ease.evaluate_vectors(np.asarray((25.0,)))[:, 0],
            (1.0 / 12.0,),
        )

    def test_evaluates_tcb_quaternion_ease_with_squad(self):
        half_angle = np.sqrt(0.5)
        rotation = Chunk(
            0x442313,
            chunks={
                0x2522: Chunk(
                    0x2522,
                    data=key_data(
                        92,
                        [
                            (0, (0.0, 0.0, 0.0, 1.0)),
                            (100, (0.0, 0.0, half_angle, half_angle)),
                        ],
                        fields=[
                            {24: (0.0, 0.0, 0.0, 0.0, 0.5)},
                            {24: (0.0, 0.0, 0.0, 0.0, 0.0)},
                        ],
                    ),
                )
            },
        )
        track = extract_transform_tracks(
            Chunk(0x2005, references=[None, rotation, None]),
            lambda chunk: chunk.guid,
            lambda chunk: chunk.references,
        ).rotation

        quaternion = track.evaluate_quaternions(np.asarray((25.0,)))[0]
        self.assertAlmostEqual(quaternion.angle, np.deg2rad(7.5), places=5)
        self.assertAlmostEqual(quaternion.magnitude, 1.0, places=6)

    def test_converts_max_quaternion_and_scale_axis(self):
        quaternion = max_quaternion((1.0, 2.0, 3.0, 4.0))
        expected = np.asarray((4.0, -1.0, -2.0, -3.0), dtype=np.float64)
        expected /= np.linalg.norm(expected)
        np.testing.assert_allclose(tuple(quaternion), expected)

        half_angle = np.sqrt(0.5)
        matrix = max_scale_matrix(
            (2.0, 3.0, 4.0, 0.0, 0.0, half_angle, half_angle)
        )
        np.testing.assert_allclose(
            np.asarray(matrix),
            np.diag((3.0, 2.0, 4.0, 1.0)),
            atol=1e-6,
        )

    def test_rejects_malformed_key_payload(self):
        position = Chunk(
            0x2002,
            chunks={0x2513: Chunk(0x2513, data=b"\0" * 19)},
        )
        prs = Chunk(0x2005, references=[position, None, None])
        with self.assertRaisesRegex(ValueError, "expected a multiple of 20"):
            extract_transform_tracks(
                prs,
                lambda chunk: chunk.guid,
                lambda chunk: chunk.references,
            )

    def test_reads_scene_timing_from_config(self):
        import_max.CONFIG = [
            Chunk(
                0x20B0,
                chunks={
                    0x0010: Chunk(0x0010, data=struct.pack("<i", 24)),
                    0x0050: Chunk(0x0050, data=struct.pack("<i", 160)),
                    0x0060: Chunk(0x0060, data=struct.pack("<i", 4960)),
                    0x0070: Chunk(0x0070, data=struct.pack("<i", 320)),
                },
            )
        ]

        self.assertEqual(
            import_max.get_scene_timing(),
            {
                "fps": 24,
                "start_tick": 160,
                "end_tick": 4960,
                "current_tick": 320,
            },
        )

    def test_decodes_and_remaps_sparse_multi_material_ids(self):
        for chunk_type, header_size in ((0x000A, 11), (0x000E, 15)):
            with self.subTest(chunk=hex(chunk_type)):
                data = bytearray(header_size + 4 + 10)
                struct.pack_into("<HH", data, 0, 3, 0x0801)
                struct.pack_into("<I", data, header_size, 2)
                data[header_size + 4] = 0x40
                struct.pack_into("<i", data, header_size + 5, 1)
                data[header_size + 9] = 0x40
                struct.pack_into("<i", data, header_size + 10, 3)
                parameter_block = Chunk(
                    0x82,
                    chunks={chunk_type: Chunk(chunk_type, data=bytes(data))},
                )

                self.assertEqual(
                    get_multi_material_ids(parameter_block, 2),
                    [0, 2],
                )
                np.testing.assert_array_equal(
                    remap_material_indices(
                        np.asarray((0, 2, 2), dtype=np.int32),
                        {0: 0, 2: 1},
                    ),
                    (0, 1, 1),
                )

    def test_summarizes_bounded_binary_evidence(self):
        data = bytes(range(256))

        summary = data_summary(data)

        self.assertEqual(summary["length"], 256)
        self.assertEqual(summary["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(
            summary["hex"],
            data[:128].hex(" "),
        )

    def test_pairs_derived_modifier_and_mod_app_slots(self):
        modifiers = [
            SimpleNamespace(types=0x100),
            None,
        ]
        mod_apps = [
            SimpleNamespace(types=0x2500),
            SimpleNamespace(types=0x2500),
        ]
        base_object = SimpleNamespace(types=0x0914)
        wrapper = SimpleNamespace(
            types=0x2032,
            children=mod_apps,
        )

        def summarize(chunk, _depth, _seen):
            return None if chunk is None else {"type": chunk.types}

        with (
            patch(
                "tools.inspect_max.import_max.get_references",
                return_value=[*modifiers, base_object],
            ),
            patch("tools.inspect_max.import_max.get_guid", return_value=0),
            patch("tools.inspect_max.chunk_summary", side_effect=summarize),
        ):
            stacks = derived_stack_summaries(wrapper, 4)

        self.assertEqual(len(stacks), 1)
        self.assertEqual(stacks[0]["slots"][0]["modifier"]["type"], 0x100)
        self.assertEqual(stacks[0]["slots"][0]["mod_app"]["type"], 0x2500)
        self.assertIsNone(stacks[0]["slots"][1]["modifier"])
        self.assertEqual(
            stacks[0]["base_object"]["type"],
            base_object.types,
        )

    def test_decodes_editable_mesh_smoothing_groups(self):
        face_flags = (7 << 16) | 5
        data = struct.pack(
            "<6I",
            1,
            0,
            1,
            2,
            0x80000001,
            face_flags,
        )

        faces, materials, smoothing = get_mesh_polys(data)

        np.testing.assert_array_equal(faces, ((0, 1, 2),))
        np.testing.assert_array_equal(materials, (7,))
        np.testing.assert_array_equal(smoothing, (0x80000001,))

    def test_decodes_editable_poly_smoothing_groups(self):
        data = struct.pack(
            "<5IHI",
            1,
            3,
            0,
            1,
            2,
            0x10,
            0x40000002,
        )
        points = calc_point_3d(Chunk(0x011A, data=data))
        mesh = Mesh3d()
        mesh.set(points)

        self.assertEqual(mesh.faces, [(0, 1, 2)])
        self.assertEqual(mesh.smoothing, [0x40000002])

    def test_applies_smoothing_groups_to_blender_edges_and_normals(self):
        vertices = np.asarray(
            (
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0),
                (0.0, 0.0, 1.0),
            ),
            dtype=np.float32,
        )
        faces = [(0, 1, 2), (0, 3, 1)]

        smooth_shape = draw_shape(
            "OverlappingGroups",
            SimpleNamespace(verts=vertices),
            faces,
        )
        apply_smoothing_groups(smooth_shape, (1, 3))
        smooth_shape.update()

        split_shape = draw_shape(
            "DisjointGroups",
            SimpleNamespace(verts=vertices),
            faces,
        )
        apply_smoothing_groups(split_shape, (0x80000001, 2))
        split_shape.update()

        flat_shape = draw_shape(
            "ZeroGroup",
            SimpleNamespace(verts=vertices),
            faces,
        )
        apply_smoothing_groups(flat_shape, (0, 1))

        def shared_edge(shape):
            return next(
                edge
                for edge in shape.edges
                if set(edge.vertices) == {0, 1}
            )

        self.assertFalse(shared_edge(smooth_shape).use_edge_sharp)
        self.assertTrue(shared_edge(split_shape).use_edge_sharp)
        self.assertTrue(shared_edge(flat_shape).use_edge_sharp)
        self.assertTrue(all(polygon.use_smooth for polygon in split_shape.polygons))
        self.assertFalse(flat_shape.polygons[0].use_smooth)

        stored = np.empty(2, dtype=np.int32)
        split_shape.attributes["max_smoothing_group"].data.foreach_get(
            "value",
            stored,
        )
        np.testing.assert_array_equal(
            stored.view(np.uint32),
            (0x80000001, 2),
        )

        smooth_normals = [
            smooth_shape.corner_normals[0].vector,
            smooth_shape.corner_normals[3].vector,
        ]
        split_normals = [
            split_shape.corner_normals[0].vector,
            split_shape.corner_normals[3].vector,
        ]
        np.testing.assert_allclose(smooth_normals[0], smooth_normals[1])
        self.assertLess(split_normals[0].dot(split_normals[1]), 0.1)

    def test_samples_frames_and_writes_layered_action(self):
        position = Chunk(
            0x2008,
            chunks={
                0x2524: Chunk(
                    0x2524,
                    data=key_data(
                        56,
                        [(0, (0.0, 0.0, 0.0)), (640, (2.0, 0.0, 0.0))],
                    ),
                )
            },
        )
        prs = Chunk(0x2005, references=[position, None, None])
        tracks = extract_transform_tracks(
            prs,
            lambda chunk: chunk.guid,
            lambda chunk: chunk.references,
        )
        samples = sample_transform(
            tracks,
            Matrix.Identity(4),
            Matrix.Identity(4),
            fps=30.0,
            frame_offset=1.0,
            sampling="BAKE",
        )

        np.testing.assert_allclose(samples.frames, (1.0, 2.0, 3.0, 4.0, 5.0))
        np.testing.assert_allclose(
            samples.locations[:, 0],
            (0.0, 0.3125, 1.0, 1.6875, 2.0),
        )

        obj = bpy.data.objects.new("Animated", None)
        bpy.context.scene.collection.objects.link(obj)
        builder = ActionBuilder("Layered")
        builder.add_object(obj, samples)
        action = builder.finish()

        self.assertIsNotNone(action)
        self.assertEqual(len(action.slots), 1)
        channelbag = action.layers[0].strips[0].channelbag(action.slots[0])
        self.assertEqual(len(channelbag.fcurves), 10)
        self.assertEqual(
            sum(len(fcurve.keyframe_points) for fcurve in channelbag.fcurves),
            50,
        )


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
