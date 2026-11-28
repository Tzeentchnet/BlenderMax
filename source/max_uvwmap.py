# SPDX-FileCopyrightText: 2026 BlenderMax contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Evaluate the 3ds Max UVW Map modifier on base geometry.

The modifier projects object-space points into a unit gizmo and writes
per-face-corner texture coordinates. The gizmo-to-object transform is, in
column-vector order::

    inverse(ModContext) @ GizmoPRS @ IconRotation @ AxisRotation @ Size

* ``ModContext`` maps object space to modifier-context space (ModApp 0x2510).
* ``GizmoPRS`` is the modifier's Position/Rotation/Scale controller value.
* ``IconRotation`` is the 3ds Max 3+ icon pre-rotation: 180 degrees about Z
  for planar and box mapping, 90 degrees for cylindrical mapping.
* ``AxisRotation`` aligns the gizmo to the X or Y axis when requested.
* ``Size`` scales by width (X), length (Y), and height (Z).

In unit gizmo space:

* planar mapping is ``u = x + 0.5``, ``v = y + 0.5``;
* box mapping projects each face along the gizmo axis nearest its normal,
  so that every side reads unmirrored from outside;
* cylindrical mapping is ``u = atan2(y, x) / 2pi + 0.5``, ``v = z + 0.5``,
  with optional planar caps on faces whose normal is closest to Z.

These conventions were checked against per-corner texture coordinates that
3ds Max exported from the same scenes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


MAP_PLANAR = 0
MAP_CYLINDRICAL = 1
MAP_SPHERICAL = 2
MAP_BALL = 3
MAP_BOX = 4
MAP_FACE = 5

SUPPORTED_MAP_TYPES = {MAP_PLANAR, MAP_CYLINDRICAL, MAP_BOX}

# Legacy IParamBlock indices of the UVW Map modifier.
PB_MAPTYPE = 0
PB_UTILE = 1
PB_VTILE = 2
PB_WTILE = 3
PB_UFLIP = 4
PB_VFLIP = 5
PB_WFLIP = 6
PB_CAP = 7
PB_CHANNEL = 8
PB_LENGTH = 9
PB_WIDTH = 10
PB_HEIGHT = 11
PB_AXIS = 12


@dataclass(frozen=True)
class UVWMapSettings:
    map_type: int = MAP_PLANAR
    tile: tuple[float, float, float] = (1.0, 1.0, 1.0)
    flip: tuple[bool, bool, bool] = (False, False, False)
    cap: bool = False
    channel: int = 0
    length: float = 1.0
    width: float = 1.0
    height: float = 1.0
    axis: int = 2

    @classmethod
    def from_paramblock(cls, values: dict[int, object]) -> "UVWMapSettings":
        def number(index, default):
            value = values.get(index, default)
            return float(value) if isinstance(value, (int, float)) else default

        def integer(index, default):
            value = values.get(index, default)
            return int(value) if isinstance(value, (int, float)) else default

        return cls(
            map_type=integer(PB_MAPTYPE, MAP_PLANAR),
            tile=(number(PB_UTILE, 1.0), number(PB_VTILE, 1.0), number(PB_WTILE, 1.0)),
            flip=(
                bool(integer(PB_UFLIP, 0)),
                bool(integer(PB_VFLIP, 0)),
                bool(integer(PB_WFLIP, 0)),
            ),
            cap=bool(integer(PB_CAP, 0)),
            channel=integer(PB_CHANNEL, 0),
            length=number(PB_LENGTH, 1.0),
            width=number(PB_WIDTH, 1.0),
            height=number(PB_HEIGHT, 1.0),
            axis=integer(PB_AXIS, 2),
        )


def _rotation(axis: str, angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    m = np.eye(4)
    i, j = {"x": (1, 2), "y": (2, 0), "z": (0, 1)}[axis]
    m[i, i] = c
    m[i, j] = -s
    m[j, i] = s
    m[j, j] = c
    return m


def gizmo_to_context(
    settings: UVWMapSettings,
    gizmo: np.ndarray,
    rotate_icon: bool = True,
) -> np.ndarray:
    """Column-vector matrix from unit gizmo space to modifier-context space."""
    matrix = np.asarray(gizmo, dtype=np.float64)
    if rotate_icon:
        angle = math.pi / 2 if settings.map_type == MAP_CYLINDRICAL else math.pi
        matrix = matrix @ _rotation("z", angle)
    if settings.axis == 0:
        matrix = matrix @ _rotation("y", -math.pi / 2)
    elif settings.axis == 1:
        matrix = matrix @ _rotation("x", math.pi / 2)
    size = [settings.width, settings.length, settings.height]
    if settings.map_type == MAP_PLANAR:
        size[2] = 1.0
    size = [value if abs(value) > 1e-12 else 1.0 for value in size]
    return matrix @ np.diag((*size, 1.0))


def object_to_gizmo(
    settings: UVWMapSettings,
    gizmo: np.ndarray,
    context: np.ndarray,
    rotate_icon: bool = True,
) -> np.ndarray:
    """Column-vector matrix from object space to unit gizmo space."""
    to_context = gizmo_to_context(settings, gizmo, rotate_icon)
    return np.linalg.inv(to_context) @ np.asarray(context, dtype=np.float64)


def _polygon_normals(points, loop_vertices, loop_start, loop_total):
    """Newell normals of each polygon from already-transformed points."""
    corners = points[loop_vertices]
    following = np.arange(len(loop_vertices)) + 1
    following[loop_start + loop_total - 1] = loop_start
    nxt = corners[following]
    terms = np.stack(
        (
            (corners[:, 1] - nxt[:, 1]) * (corners[:, 2] + nxt[:, 2]),
            (corners[:, 2] - nxt[:, 2]) * (corners[:, 0] + nxt[:, 0]),
            (corners[:, 0] - nxt[:, 0]) * (corners[:, 1] + nxt[:, 1]),
        ),
        axis=1,
    )
    face_index = np.repeat(np.arange(len(loop_start)), loop_total)
    normals = np.zeros((len(loop_start), 3))
    np.add.at(normals, face_index, terms)
    return normals


def map_corners(
    settings: UVWMapSettings,
    transform: np.ndarray,
    positions: np.ndarray,
    loop_vertices: np.ndarray,
    loop_start: np.ndarray,
    loop_total: np.ndarray,
) -> np.ndarray:
    """Return (loops, 3) UVW coordinates for the given polygon corners.

    ``transform`` maps object space to unit gizmo space (see object_to_gizmo).
    """
    if settings.map_type not in SUPPORTED_MAP_TYPES:
        raise ValueError("unsupported UVW Map type %d" % settings.map_type)
    positions = np.asarray(positions, dtype=np.float64).reshape(-1, 3)
    loop_vertices = np.asarray(loop_vertices, dtype=np.int64)
    loop_start = np.asarray(loop_start, dtype=np.int64)
    loop_total = np.asarray(loop_total, dtype=np.int64)
    homogeneous = np.c_[positions, np.ones(len(positions))]
    points = (homogeneous @ np.asarray(transform, dtype=np.float64).T)[:, :3]
    corners = points[loop_vertices]
    x, y, z = corners[:, 0], corners[:, 1], corners[:, 2]
    face_index = np.repeat(np.arange(len(loop_start)), loop_total)
    uvw = np.zeros((len(loop_vertices), 3))

    if settings.map_type == MAP_PLANAR:
        uvw[:, 0] = x + 0.5
        uvw[:, 1] = y + 0.5
        uvw[:, 2] = z

    elif settings.map_type == MAP_BOX:
        normal = _polygon_normals(points, loop_vertices, loop_start, loop_total)[face_index]
        dominant = np.argmax(np.abs(normal), axis=1)
        positive = normal[np.arange(len(normal)), dominant] >= 0.0
        side_x = dominant == 0
        side_y = dominant == 1
        side_z = dominant == 2
        uvw[side_x, 0] = np.where(positive[side_x], y[side_x], -y[side_x])
        uvw[side_x, 1] = z[side_x]
        uvw[side_x, 2] = x[side_x]
        uvw[side_y, 0] = np.where(positive[side_y], -x[side_y], x[side_y])
        uvw[side_y, 1] = z[side_y]
        uvw[side_y, 2] = y[side_y]
        uvw[side_z, 0] = np.where(positive[side_z], x[side_z], -x[side_z])
        uvw[side_z, 1] = y[side_z]
        uvw[side_z, 2] = z[side_z]
        uvw[:, :2] += 0.5

    elif settings.map_type == MAP_CYLINDRICAL:
        u = np.arctan2(y, x) / (2.0 * math.pi) + 0.5
        # Keep each polygon on one side of the seam instead of wrapping.
        reference = u[loop_start][face_index]
        u = np.where(u - reference > 0.5, u - 1.0, u)
        u = np.where(reference - u > 0.5, u + 1.0, u)
        uvw[:, 0] = u
        uvw[:, 1] = z + 0.5
        uvw[:, 2] = np.hypot(x, y)
        if settings.cap:
            normal = _polygon_normals(points, loop_vertices, loop_start, loop_total)[face_index]
            cap = np.abs(normal[:, 2]) > np.maximum(np.abs(normal[:, 0]), np.abs(normal[:, 1]))
            uvw[cap, 0] = np.where(normal[cap, 2] >= 0.0, x[cap], -x[cap]) + 0.5
            uvw[cap, 1] = y[cap] + 0.5
            uvw[cap, 2] = z[cap]

    for axis in range(3):
        if settings.flip[axis]:
            uvw[:, axis] = 1.0 - uvw[:, axis]
        uvw[:, axis] *= settings.tile[axis]
    return uvw
