# SPDX-License-Identifier: GPL-3.0-or-later

"""Layouts used by 3ds Max 2-4 era scenes: legacy parameter blocks, Standard
materials, UVW Map modifiers, and tolerant mesh/OLE handling."""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy
import numpy as np


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from source import import_max  # noqa: E402
from source.import_max import (  # noqa: E402
    MaterialSlotMap,
    Mesh3d,
    apply_edit_mesh,
    apply_unwrap,
    create_shape,
    delete_sequence,
    draw_shape,
    face_sources,
    get_context_matrix,
    get_edit_mesh,
    get_unwrap_uvs,
    get_legacy_standard_material,
    get_paramblock_values,
    remap_material_indices,
    sanitize_faces,
)
from source.max_uvwmap import (  # noqa: E402
    MAP_BOX,
    MAP_CYLINDRICAL,
    MAP_FACE,
    MAP_PLANAR,
    UVWMapSettings,
    map_corners,
    object_to_gizmo,
)


class Node:
    """Chunk stand-in: ordered children, keyed references, and a class ID."""

    def __init__(self, types, children=(), data=b"", guid=None, keyed=None):
        self.types = types
        self.children = list(children)
        self.data = data
        self.guid = types if guid is None else guid
        self.keyed = keyed or {}

    def get_first(self, types):
        return next((child for child in self.children if child.types == types), None)


def parameter(index, chunk_type=None, value=None):
    children = [Node(0x0003, data=struct.pack("<i", index)), Node(0x0004)]
    if chunk_type == 0x0100:
        children.append(Node(0x0100, data=struct.pack("<f", value)))
    elif chunk_type == 0x0101:
        children.append(Node(0x0101, data=struct.pack("<i", value)))
    elif chunk_type == 0x0102:
        children.append(Node(0x0102, data=tuple(value)))
    return Node(0x0002, children)


def paramblock(*parameters):
    return Node(
        0x0000,
        [Node(0x0001, data=struct.pack("<I", len(parameters))), *parameters],
        guid=0x0008,
    )


def legacy_bitmap(path):
    info = Node(0x5010, [Node(0x1230, data=path)])
    return Node(0x0004, [info], guid=0x0240)


class LegacyMaterialTests(unittest.TestCase):
    def test_decodes_legacy_paramblock_inline_values(self):
        block = paramblock(
            parameter(0, 0x0101, 4),
            parameter(1, 0x0100, 1.5),
            parameter(2, 0x0102, (0.25, 0.5, 0.75)),
            parameter(3),  # animated: value lives in a controller
        )

        self.assertEqual(
            get_paramblock_values(block),
            {0: 4, 1: 1.5, 2: (0.25, 0.5, 0.75)},
        )

    def _material(self, mask):
        block = paramblock(
            parameter(1, 0x0102, (0.5, 0.25, 0.125)),
            parameter(3, 0x0100, 0.25),
            parameter(4, 0x0100, 0.05),
            parameter(5, 0x0100, 0.0),
            parameter(6, 0x0100, 0.5),
            parameter(10, 0x0100, 1.5),
        )
        texmaps = Node(
            0x0005,
            [Node(0x5003, data=struct.pack("<I", mask))],
            guid=0x1200,
            keyed={3: legacy_bitmap("X:\\Depot\\Textures\\wall.bmp")},
        )
        with (
            patch.object(import_max, "get_guid", side_effect=lambda chunk: chunk.guid),
            patch.object(import_max, "get_reference", side_effect=lambda chunk: chunk.keyed),
        ):
            return get_legacy_standard_material([block, texmaps])

    def test_decodes_legacy_standard_material(self):
        material = self._material(mask=1 << 1)

        self.assertEqual(material.get("diffuse"), (0.5, 0.25, 0.125))
        self.assertAlmostEqual(material.get("shinines"), 0.25)
        self.assertAlmostEqual(material.get("glossines"), 0.05)
        self.assertAlmostEqual(material.get("opacity"), 0.5)
        self.assertAlmostEqual(material.get("refraction"), 1.5)
        self.assertIsNone(material.get("emissive"))
        self.assertEqual(material.get("bitmap"), "X:\\Depot\\Textures\\wall.bmp")

    def test_skips_texmaps_disabled_in_slot_mask(self):
        self.assertIsNone(self._material(mask=0).get("bitmap"))

    def test_dense_multi_material_ids_wrap_around(self):
        slots = MaterialSlotMap({0: 0, 1: 1})
        slots.cycle = 2

        np.testing.assert_array_equal(
            remap_material_indices(np.asarray((0, 1, 2, 3, 5)), slots),
            (0, 1, 0, 1, 1),
        )


class MeshRobustnessTests(unittest.TestCase):
    def test_sanitize_keeps_double_sided_faces_and_aligns_face_data(self):
        mesh = Mesh3d()
        mesh.verts = np.zeros(12, dtype=np.float32)
        mesh.faces = np.asarray(
            ((0, 1, 2), (2, 1, 0), (0, 0, 1), (0, 1, 9), (1, 2, 3)),
            dtype=np.uint32,
        )
        mesh.mats = np.arange(5, dtype=np.int32)
        mesh.smoothing = np.arange(10, 15, dtype=np.uint32)
        mesh.uvids = [np.arange(15).reshape(5, 3)]

        self.assertEqual(sanitize_faces(mesh), 2)
        np.testing.assert_array_equal(mesh.faces, ((0, 1, 2), (2, 1, 0), (1, 2, 3)))
        np.testing.assert_array_equal(mesh.mats, (0, 1, 4))
        np.testing.assert_array_equal(mesh.smoothing, (10, 11, 14))
        np.testing.assert_array_equal(mesh.uvids[0], ((0, 1, 2), (3, 4, 5), (12, 13, 14)))

        shape = draw_shape("double sided", mesh, mesh.faces)
        shape.update(calc_edges=True)
        self.assertEqual(len(shape.polygons), 3)
        bpy.data.meshes.remove(shape)

    def test_sanitize_filters_polygon_lists(self):
        mesh = Mesh3d()
        mesh.verts = np.zeros(12, dtype=np.float32)
        mesh.faces = [(0, 1, 2, 3), (0, 1, 1), (0, 1, 2, 7)]
        mesh.mats = [1, 2, 3]

        self.assertEqual(sanitize_faces(mesh), 2)
        self.assertEqual(mesh.faces, [(0, 1, 2, 3)])
        self.assertEqual(mesh.mats, [1])

    def test_draws_mesh_without_vertices(self):
        mesh = Mesh3d()
        mesh.verts = np.empty(0, dtype=np.float32)
        shape = draw_shape("empty", mesh, [])
        self.assertEqual(len(shape.vertices), 0)
        bpy.data.meshes.remove(shape)

    def test_skips_directory_entries_past_truncated_stream(self):
        entry = object.__new__(import_max.MaxFileDirEntry)
        entry.maxfile = SimpleNamespace(direntries=[None, None])
        entry.kids = []
        entry.kids_dict = {}

        entry.append_kids(16)

        self.assertEqual(entry.kids, [])


def unwrap_modifier(vertices, faces):
    vertices = np.asarray(vertices, dtype="<f4")
    faces = np.asarray(faces, dtype="<u4")
    return Node(
        0x0011,
        [
            Node(0x0100, data=struct.pack("<I", len(vertices))),
            Node(0x0110, data=vertices.tobytes()),
            Node(0x0230, data=struct.pack("<I", len(faces))),
            Node(0x0240, data=faces.tobytes()),
        ],
    )


class UnwrapTests(unittest.TestCase):
    def setUp(self):
        face_sources.clear()

    def tearDown(self):
        face_sources.clear()
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj)

    def _triangle_pair(self, extra_degenerate=False):
        mesh = Mesh3d()
        mesh.verts = np.asarray(
            ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)), dtype=np.float32
        ).ravel()
        faces = [(0, 1, 2), (2, 3, 0)]
        if extra_degenerate:
            faces.insert(1, (0, 0, 1))
        mesh.faces = np.asarray(faces, dtype=np.uint32)
        node = SimpleNamespace(get_first=lambda chunk_type: None)
        settings = ("scene.max", {"UV"}, False, None)
        return create_shape(bpy.context, settings, node, mesh, None)[-1]

    def test_decodes_unwrap_payload(self):
        vertices, faces = get_unwrap_uvs(
            unwrap_modifier(((0, 0, 0), (1, 0, 0), (1, 1, 0)), ((0, 1, 2),))
        )

        np.testing.assert_allclose(vertices[2], (1, 1, 0))
        np.testing.assert_array_equal(faces, ((0, 1, 2),))

    def test_rejects_inconsistent_unwrap_payload(self):
        modifier = unwrap_modifier(((0, 0, 0),), ((0, 1, 2),))
        self.assertIsNone(get_unwrap_uvs(modifier))

    def test_applies_unwrap_by_source_face_index(self):
        obj = self._triangle_pair(extra_degenerate=True)
        vertices = ((0.1, 0.1, 0), (0.9, 0.1, 0), (0.9, 0.9, 0), (0.1, 0.9, 0), (0.5, 0.5, 0))
        # The degenerate middle face is dropped on import; its Unwrap face
        # must be skipped rather than shifting the faces after it.
        faces = ((0, 1, 2), (4, 4, 4), (2, 3, 0))

        self.assertTrue(apply_unwrap(obj, get_unwrap_uvs(unwrap_modifier(vertices, faces))))
        uv = np.empty(len(obj.data.loops) * 2)
        obj.data.uv_layers[0].data.foreach_get("uv", uv)
        np.testing.assert_allclose(
            uv.reshape(-1, 2),
            ((0.1, 0.1), (0.9, 0.1), (0.9, 0.9), (0.9, 0.9), (0.1, 0.9), (0.1, 0.1)),
            atol=1e-6,
        )

    def test_skips_unwrap_with_different_face_count(self):
        obj = self._triangle_pair()
        unwrap = get_unwrap_uvs(unwrap_modifier(((0, 0, 0),), ((0, 0, 0),)))

        self.assertFalse(apply_unwrap(obj, unwrap))
        self.assertEqual(len(obj.data.uv_layers), 0)


def u32s(*values):
    return struct.pack("<%dI" % len(values), *values)


def edit_mesh_app(vertex_count, face_count, *, offsets=None, vertex_deletes=None,
                  face_deletes=None, faces=None, smoothing=None, output_vertices=None):
    delta = [Node(0x2810, data=u32s(face_count)), Node(0x2820, data=u32s(vertex_count))]
    if vertex_deletes is not None:
        delta.append(Node(0x2790, data=u32s(*vertex_deletes)))
    if face_deletes is not None:
        delta.append(Node(0x2800, data=u32s(*face_deletes)))
    if faces is not None:
        delta.append(Node(0x2780, data=u32s(*np.asarray(faces).ravel().tolist())))
    if smoothing is not None:
        delta.append(Node(0x2840, data=u32s(*smoothing)))
    local = [Node(0x2740, data=u32s(4)), Node(0x2765, delta)]
    if offsets is not None:
        local.append(Node(0x2760, data=np.asarray(offsets, dtype="<f4").tobytes()))
    if output_vertices is not None:
        local.append(Node(0x3000, data=u32s(output_vertices)))
    return Node(0x2500, [Node(0x2512, local)])


class EditMeshTests(unittest.TestCase):
    def setUp(self):
        face_sources.clear()

    def tearDown(self):
        face_sources.clear()
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj)

    def _two_quads(self):
        """Two separate quads (8 vertices, 4 triangles) with UVs and smoothing."""
        mesh = Mesh3d()
        mesh.verts = np.asarray(
            ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
             (5, 0, 0), (6, 0, 0), (6, 1, 0), (5, 1, 0)), dtype=np.float32
        ).ravel()
        mesh.faces = np.asarray(((0, 1, 2), (2, 3, 0), (4, 5, 6), (6, 7, 4)), dtype=np.uint32)
        mesh.smoothing = np.asarray((1, 1, 2, 2), dtype=np.uint32)
        mesh.maps = [0]
        mesh.cords = [np.asarray([(i / 10, i / 20, 0) for i in range(8)], dtype=np.float32).ravel()]
        mesh.uvids = [mesh.faces.copy()]
        node = SimpleNamespace(get_first=lambda chunk_type: None)
        settings = ("scene.max", {"UV"}, False, None)
        return create_shape(bpy.context, settings, node, mesh, None)[-1]

    def test_deletions_apply_to_the_shrinking_list(self):
        np.testing.assert_array_equal(delete_sequence(5, [3, 3]), (0, 1, 2))
        np.testing.assert_array_equal(delete_sequence(6, [0, 0, 2]), (2, 3, 5))
        self.assertIsNone(delete_sequence(2, [2]))
        self.assertIsNone(delete_sequence(2, [0, 0, 0]))
        rng = np.random.default_rng(7)
        for count in (1, 2, 17, 300):
            alive = list(range(count))
            deletions = []
            for _ in range(int(rng.integers(0, count + 1))):
                position = int(rng.integers(0, len(alive)))
                deletions.append(position)
                del alive[position]
            np.testing.assert_array_equal(delete_sequence(count, deletions), alive)

    def test_stale_input_counts_are_rejected_before_allocation(self):
        # A corrupt 0x2820 count must not size any array.
        app = edit_mesh_app(0xFFFFFFF0, 4, vertex_deletes=(0,))
        self.assertIsNone(get_edit_mesh(app, 8, 4))

    def test_vertex_offsets_move_input_vertices(self):
        obj = self._two_quads()
        offsets = np.zeros((8, 3)); offsets[1] = (0, 0, 3)
        edit = get_edit_mesh(edit_mesh_app(8, 4, offsets=offsets), 8, 4)

        self.assertTrue(apply_edit_mesh(obj, edit))
        self.assertEqual(tuple(obj.data.vertices[1].co), (1.0, 0.0, 3.0))
        self.assertEqual(len(obj.data.polygons), 4)

    def test_deletes_faces_and_vertices_and_carries_face_data(self):
        obj = self._two_quads()
        # Delete the first quad: vertices 0-3 and faces 0-1. Output faces use
        # the renumbered vertices 0-3 (former 4-7) and new smoothing groups.
        edit = get_edit_mesh(edit_mesh_app(
            8, 4,
            vertex_deletes=(0, 0, 0, 0),
            face_deletes=(0, 0),
            faces=((0, 1, 2), (2, 3, 0)),
            smoothing=(4, 4),
            output_vertices=4,
        ), 8, 4)

        self.assertTrue(apply_edit_mesh(obj, edit))
        mesh = obj.data
        self.assertEqual(len(mesh.vertices), 4)
        self.assertEqual(tuple(mesh.vertices[0].co), (5.0, 0.0, 0.0))
        uv = np.empty(len(mesh.loops) * 2); mesh.uv_layers[0].data.foreach_get("uv", uv)
        np.testing.assert_allclose(uv.reshape(-1, 2)[0], (0.4, 0.2), atol=1e-6)
        groups = np.empty(2, dtype=np.int32)
        mesh.attributes["max_smoothing_group"].data.foreach_get("value", groups)
        np.testing.assert_array_equal(groups, (4, 4))
        # An Unwrap above pairs with the two output faces.
        self.assertEqual(face_sources[mesh.as_pointer()][0], 2)

    def test_rejects_mismatched_input_and_unknown_operations(self):
        obj = self._two_quads()
        self.assertIsNone(get_edit_mesh(edit_mesh_app(9, 4), 8, 4))
        self.assertIsNone(get_edit_mesh(edit_mesh_app(8, 4, output_vertices=7), 8, 4))
        app = edit_mesh_app(8, 4)
        app.children[0].children[1].children.append(Node(0x2771, data=bytes(12)))
        self.assertIsNone(get_edit_mesh(app, 8, 4))


class UVWMapTests(unittest.TestCase):
    QUAD = (
        np.asarray(((-1, -2, 0), (1, -2, 0), (1, 2, 0), (-1, 2, 0)), dtype=np.float64),
        np.asarray((0, 1, 2, 3)),
        np.asarray((0,)),
        np.asarray((4,)),
    )

    def test_context_matrix_from_row_vector_mod_app(self):
        # Rotate 90 degrees about Z, then translate by (10, 20, 30).
        rows = (0.0, 1.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 10.0, 20.0, 30.0, 4)
        matrix = get_context_matrix(Node(0x2500, [Node(0x2510, data=rows)]))

        np.testing.assert_allclose(matrix @ (1.0, 0.0, 0.0, 1.0), (10.0, 21.0, 30.0, 1.0))

    def test_planar_mapping_applies_icon_rotation_size_and_tiling(self):
        settings = UVWMapSettings(map_type=MAP_PLANAR, width=2.0, length=4.0, tile=(2.0, 1.0, 1.0))
        transform = object_to_gizmo(settings, np.eye(4), np.eye(4))

        uvw = map_corners(settings, transform, *self.QUAD)

        # The icon pre-rotation turns the gizmo 180 degrees about Z.
        np.testing.assert_allclose(uvw[:, 0], (2.0, 0.0, 0.0, 2.0), atol=1e-12)
        np.testing.assert_allclose(uvw[:, 1], (1.0, 1.0, 0.0, 0.0), atol=1e-12)

    def test_planar_mapping_follows_gizmo_and_context(self):
        settings = UVWMapSettings(map_type=MAP_PLANAR, width=2.0, length=4.0)
        gizmo = np.eye(4)
        gizmo[:3, 3] = (1.0, 0.0, 0.0)
        context = np.eye(4)
        context[:3, 3] = (-1.0, 0.0, 0.0)
        transform = object_to_gizmo(settings, gizmo, context, rotate_icon=False)

        uvw = map_corners(settings, transform, *self.QUAD)

        # Object x -> context x - 1 -> gizmo (x - 2) / width.
        np.testing.assert_allclose(uvw[:, 0], (-1.0, 0.0, 0.0, -1.0))

    def test_box_mapping_projects_each_side_unmirrored(self):
        settings = UVWMapSettings(map_type=MAP_BOX)
        transform = object_to_gizmo(settings, np.eye(4), np.eye(4), rotate_icon=False)
        point = (0.2, 0.3, 0.1)
        expected = {
            (1, 0, 0): (0.3, 0.1),
            (-1, 0, 0): (-0.3, 0.1),
            (0, 1, 0): (-0.2, 0.1),
            (0, -1, 0): (0.2, 0.1),
            (0, 0, 1): (0.2, 0.3),
            (0, 0, -1): (-0.2, 0.3),
        }
        for normal, (u, v) in expected.items():
            with self.subTest(normal=normal):
                normal = np.asarray(normal, dtype=np.float64)
                tangent = np.roll(np.abs(normal), 1)
                bitangent = np.cross(normal, tangent)
                corners = np.asarray((point, point + tangent * 0.1, point + bitangent * 0.1))
                uvw = map_corners(
                    settings,
                    transform,
                    corners,
                    np.asarray((0, 1, 2)),
                    np.asarray((0,)),
                    np.asarray((3,)),
                )
                np.testing.assert_allclose(uvw[0, :2], (u + 0.5, v + 0.5), atol=1e-12)

    def test_cylindrical_mapping_seam_and_height(self):
        settings = UVWMapSettings(map_type=MAP_CYLINDRICAL, height=2.0)
        transform = object_to_gizmo(settings, np.eye(4), np.eye(4), rotate_icon=False)
        angles = np.radians((170.0, -170.0, -170.0, 170.0))
        heights = (0.0, 0.0, 1.0, 1.0)
        corners = np.c_[np.cos(angles), np.sin(angles), heights]

        uvw = map_corners(
            settings,
            transform,
            corners,
            np.arange(4),
            np.asarray((0,)),
            np.asarray((4,)),
        )

        self.assertLess(np.ptp(uvw[:, 0]), 0.1)
        np.testing.assert_allclose(uvw[:, 1], (0.5, 0.5, 1.0, 1.0))
        point = map_corners(
            settings,
            transform,
            np.asarray(((0.0, 1.0, 0.0), (0.0, 1.0, 0.1), (0.0, 1.1, 0.0))),
            np.arange(3),
            np.asarray((0,)),
            np.asarray((3,)),
        )
        self.assertAlmostEqual(point[0, 0], 0.75)

    def test_flip_mirrors_before_tiling(self):
        settings = UVWMapSettings(
            map_type=MAP_PLANAR, width=2.0, flip=(True, False, False), tile=(3.0, 1.0, 1.0)
        )
        transform = object_to_gizmo(settings, np.eye(4), np.eye(4), rotate_icon=False)

        uvw = map_corners(settings, transform, *self.QUAD)

        np.testing.assert_allclose(uvw[:, 0], (3.0, 0.0, 0.0, 3.0))

    def test_rejects_unverified_map_types(self):
        settings = UVWMapSettings(map_type=MAP_FACE)
        with self.assertRaises(ValueError):
            map_corners(settings, np.eye(4), *self.QUAD)

    def test_settings_from_legacy_paramblock(self):
        settings = UVWMapSettings.from_paramblock(
            {0: 4, 1: 2.0, 2: 3.0, 3: 1.0, 4: 1, 5: 0, 6: 0, 7: 0, 8: 0,
             9: 10.0, 10: 20.0, 11: 30.0, 12: 1}
        )

        self.assertEqual(settings.map_type, MAP_BOX)
        self.assertEqual(settings.tile, (2.0, 3.0, 1.0))
        self.assertEqual(settings.flip, (True, False, False))
        self.assertEqual((settings.length, settings.width, settings.height), (10.0, 20.0, 30.0))
        self.assertEqual(settings.axis, 1)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
