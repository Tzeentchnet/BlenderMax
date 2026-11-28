# Scene data, geometry, and materials

## Scene nodes

The importer recognizes scene-node chunks by their resolved class and
superclass IDs. The node class observed by BlenderMax uses combined class ID
`0x0000000000000001` and superclass ID `0x00000001`. Chunks with a valid
parent reference are also considered node candidates.

Known node-local chunks:

| Chunk | Interpretation | Status |
|---|---|---|
| `0x0960` | Parent Scene child index (`uint32`) | Confirmed |
| `0x0962` | Node name (UTF-16LE) | Confirmed |
| `0x096A` | Object/pivot translation (`float32[3]`) | Confirmed |
| `0x096B` | Object/pivot quaternion (`float32[4]`, `x y z w`) | Confirmed |
| `0x096C` | Object/pivot scale and optional scale-axis quaternion | Confirmed |

For node references, both keyed `0x2035` mappings and positional `0x2034`
arrays occur. The slots currently observed are:

| Slot | Meaning |
|---:|---|
| `0` | Transform controller |
| `1` | Object or mesh |
| `3` | Material |
| `6` | Layer |

Unlisted slots must be preserved. They may contain modifiers, constraints,
application data, or plug-in-specific state.

## Known object class IDs

The table uses the combined 64-bit form
`(PartB << 32) | PartA`.

| Combined class ID | Current interpretation |
|---:|---|
| `0x00000000E44F10B3` | Editable Mesh |
| `0x192F60981BF8338D` | Editable Poly |
| `0x000000005D21369A` | PolyMeshObject |
| `0x0000000000001002` | Camera |
| `0x0000000000001011` | Omni light |
| `0x0000000000001013` | Free direct light |
| `0x0000000000001020` | Camera target |
| `0x0000000000876234` | Dummy helper |
| `0x0000000000009125` | Character Studio Biped object |
| `0x77566F65081F1DFC` | Plane primitive |
| `0x00000000A86C23DD` | Cone primitive |
| `0x01BBAC5F7BC209AF` | Boolean object |

Several built-in primitive IDs also fit in the low 16 bits and are interpreted
contextually. A class ID alone does not imply that every historical parameter
block layout for that class is supported.

## Editable Mesh

An Editable Mesh payload contains a `0x08FE` mesh container.

### Vertices: `0x0914`

The payload begins with a `uint32` count followed by packed positions:

```text
uint32 count
float32 positions[count][3]
```

The decoder caps `count` to the complete records available.

### Faces, smoothing groups, and material IDs: `0x0912`

The payload begins with a `uint32` count followed by 20-byte records:

```text
offset  size  type       meaning
0       12    uint32[3]  vertex indices
12      4     uint32     smoothing-group mask
16      4     uint32     face flags; material ID occupies bits 16-31
```

Vertex indices, the smoothing mask, and the material ID are **confirmed**.
BlenderMax currently reads the low and high halves of the last word as
`uint16` face flags and material ID respectively. The detailed meanings of the
low flag bits are not yet documented.

### Mapping channels

Two families have been observed:

| Purpose | One variant | Other variants |
|---|---:|---:|
| Mapping-channel declaration | `0x0924` | `0x0959` |
| Mapping coordinates | `0x0916` | `0x2394` |
| Per-face mapping indices | `0x0918` | `0x2396` |

Coordinate payloads use the same count-plus-`float32[3]` form as positions.
Mapping-index data is read as `uint32` triples after its leading value.

## Editable Poly

Editable Poly and PolyMeshObject payloads also use a `0x08FE` container, but
their topology is distributed across different child chunks.

| Chunk | Current interpretation | Confidence |
|---:|---|---|
| `0x0100` | Count followed by 16-byte records: `uint32 index`, `float32 xyz` | Confirmed |
| `0x0108` | Variable-length polygon-loop records | Tentative |
| `0x010A` | Triangle-index array; first `uint32` is treated as an end offset | Confirmed for known variants |
| `0x0118` | Face container | Confirmed |
| `0x011A` | Variable point/topology records with flags, material group, and smoothing mask | Corroborated |
| `0x0124` | Mapping-channel number | Confirmed |
| `0x0128` | Mapping coordinates as packed `float32[3]` | Confirmed |
| `0x012B` | Variable-length per-face mapping indices | Confirmed for known variants |
| `0x0310` | Variable-length polygon index lists | Tentative |

### Face container `0x0118`

Known children:

- `0x0110`: a leading `uint32` count followed by that many `uint32` vertex
  indices.
- `0x0130`: a `uint16` face material ID.

### Variable point record `0x011A`

The current decoder reads:

```text
uint32 point_index_count
uint32 point_indices[point_index_count]
uint16 flags

if flags & 0x01:
    uint16 flag1
    uint16 flag2
if flags & 0x08:
    uint16 material_group
if flags & 0x10:
    uint32 smoothing_group
if flags & 0x20:
    uint32 extra_bits[2 * (point_index_count - 3)]
```

`material_group` is used as a face material ID. The `flags & 0x10` value is
the face's smoothing-group mask. The meanings of `flag1`, `flag2`, and
`extra_bits` remain tentative.

## Smoothing groups and generated normals

3ds Max stores a 32-bit mask on each face. A zero mask makes that face flat.
Two adjacent nonzero faces smooth across their shared edge when their masks
overlap:

```text
(face_a_smoothing & face_b_smoothing) != 0
```

BlenderMax preserves the original bit pattern in the signed 32-bit
`max_smoothing_group` face attribute. Masks with bit 31 set appear negative
through Blender's signed integer API, but the bits are unchanged.

For shading, every nonzero face is marked smooth. An edge is marked sharp when
either adjacent face has group zero or the adjacent masks do not overlap.
Boundary edges are left unmarked; the owning face's smooth/flat setting
controls them. A nonmanifold edge is sharp if any adjacent pair is
incompatible. Blender then derives per-corner split normals from these face
and edge settings.

This reconstructs normals implied by smoothing groups. It does not decode
authored `MeshNormalSpec`/`MNNormalSpec` data or apply the Edit Normals
modifier.

### Modifier-local smoothing changes

The base-object masks above are not the only smoothing data that can affect
the evaluated mesh. The externally documented Edit Mesh ModApp format reports
these additional records:

| Format | Chunk | Layout |
|---|---:|---|
| Modern mesh delta | `0x0230` | Count followed by 12-byte `(face_index, mask, values)` records |
| Legacy topology delta | `0x2840` | `uint32` smoothing masks, one per affected face |
| Legacy compact form | `0x3040` | `int32 count`, `uint32 smoothing_mask` |

The modern update is:

```text
new_group = (old_group & ~mask) | (values & mask)
```

In the legacy `0x2830` packed face-attribute stream, bit 28 means to apply
the corresponding value from `0x2840`. BlenderMax does not yet evaluate Edit
Mesh deltas, so these modifier-local changes are reported but not applied.

## Derived objects and modifier data

Object-space and world-space derived wrappers use fixed Scene chunk types
`0x2032` and `0x2033`. The observed object-space wrapper class ID is
`0x405F22F529263A68`. Its `0x2034` reference array stores modifiers from the
top of the stack down, followed by the base object; the last modifier before
the base object is applied first. A parallel orphaned `0x2500` ModApp
container exists for every modifier position, including modifiers with no
local payload. The ModApp slots pair with modifier references by position.

Common ModApp children are:

| Chunk | Interpretation |
|---:|---|
| `0x2510` | 52-byte modifier-context transform: row-major `Matrix3` plus `uint32` state flags |
| `0x2511` | Optional modifier-context bounding box |
| `0x2512` | Modifier-specific payload |

Relevant modifier class IDs are:

| Combined class ID | Class |
|---:|---|
| `0x000156660095C6A3` | Native Skin |
| `0x0000000000000100` with superclass `0x810` | Character Studio Physique |
| `0x35CA1CDE4AA52AE3` | Edit Normals |
| `0x00000000000F72B1` | UVW Map |
| `0x72BA4E1F02DF2E3A` | Unwrap UVW (3ds Max 3) |
| `0x72BA4F2902DF2E3A` | UnwrapUVW+ |
| `0x0000000000000050` | Edit Mesh (3ds Max 3) |

BlenderMax imports the base object and then evaluates the stack from the
bottom up. Edit Mesh and UVW Map are applied while the mesh still equals the
stack's state at that point, that is, while every modifier below them was
evaluated. Unwrap UVW replaces the whole channel by face index, so it is
applied whenever its face count matches the current mesh. Evaluation of
geometry-dependent modifiers stops at the first modifier that cannot be
reproduced; every modifier that was not applied is reported in the console. It does not interpret
native Skin's `0x2512` payload or create speculative vertex groups. Native
Skin and Character Studio Physique use different formats and must not share a
decoder merely because both represent deformation weights.

### UVW Map modifier

**Corroborated** for 3ds Max 3-era scenes: the decoder below reproduced the
per-corner texture coordinates that 3ds Max itself exported from the same
scenes for most objects (see the evidence note at the end of this section).

The modifier (class `0x000F72B1`, superclass `0x810`) has two references:

| Reference | Content |
|---:|---|
| 0 | Position/Rotation/Scale controller holding the gizmo transform |
| 1 | Legacy parameter block (`0x0008`, see [Legacy parameter blocks](#legacy-parameter-blocks)) |

Observed parameter indices (13 parameters; a 16-parameter variant also
exists and is decoded with the same first 13 indices):

| Index | Type | Meaning |
|---:|---|---|
| 0 | int | Map type: 0 planar, 1 cylindrical, 2 spherical, 3 shrink-wrap, 4 box, 5 face |
| 1-3 | float | U, V, W tile |
| 4-6 | int | U, V, W flip |
| 7 | int | Cylinder cap |
| 8 | int | Channel: 0 map channel, 1 vertex colour |
| 9 | float | Length (gizmo Y) |
| 10 | float | Width (gizmo X) |
| 11 | float | Height (gizmo Z) |
| 12 | int | Alignment axis: 0 X, 1 Y, 2 Z |

The modifier's ModApp `0x2510` supplies the ModContext matrix `C` (object to
context space, row-vector `Matrix3`). In column-vector form, object points map
to the unit gizmo through:

```text
inverse(G @ I @ A @ S) @ C
G  gizmo PRS controller value
I  icon pre-rotation: 180 deg about Z (planar, box), 90 deg (cylindrical)
A  axis alignment: X -> rotate Y by -90 deg; Y -> rotate X by +90 deg
S  scale(width, length, height); planar uses height 1
```

In unit gizmo coordinates `(x, y, z)`:

| Map | U | V |
|---|---|---|
| Planar | `x + 0.5` | `y + 0.5` |
| Box, normal +X / -X | `y + 0.5` / `-y + 0.5` | `z + 0.5` |
| Box, normal +Y / -Y | `-x + 0.5` / `x + 0.5` | `z + 0.5` |
| Box, normal +Z / -Z | `x + 0.5` / `-x + 0.5` | `y + 0.5` |
| Cylindrical | `atan2(y, x) / 2pi + 0.5` | `z + 0.5` |

Box mapping chooses the side from the dominant component of the face normal
in gizmo space. Flip applies `1 - value` before multiplying by the tile
factor.

Evidence: across 851 scenes with matching exporter output, corners matched
exactly for 83% of single box maps, 93-100% of stacked box maps, 78% of
planar maps, and 84% of cylindrical maps; objects with stored UVs matched
99.6%. Per-object results are bimodal (almost all corners or almost none),
consistent with exports made before later scene edits rather than a formula
error. Spherical, shrink-wrap, and face mapping, cylinder caps, and the
16-parameter variant's extra fields are **Tentative** or **Unknown** and are
not evaluated, except that cylinder caps are applied as planar projections.

BlenderMax evaluates only the contiguous UVW Maps immediately above the base
object. A UVW Map above an Edit Mesh or other modifier can depend on that
modifier's sub-object selection, which is not decoded.

### Edit Mesh modifier

**Corroborated** for the 3ds Max 3 Edit Mesh (class `0x50`, superclass
`0x810`). The modifier itself stores only editor settings; the edit lives in
its ModApp's `0x2512` container. It does not store a sequence of operations
but the result relative to the incoming mesh:

| Chunk | Content |
|---:|---|
| `0x2740` | `uint32` sub-object level |
| `0x2750` / `0x2755` / `0x2756` -> `0x2700` | Selection bit arrays (vertex / face / edge); **Tentative** |
| `0x2760` | One object-space Point3 offset per incoming vertex |
| `0x3000` | `uint32` output vertex count |
| `0x2765` | Mesh-delta container (below) |

Inside `0x2765`:

| Chunk | Content |
|---:|---|
| `0x2820` / `0x2810` | `uint32` incoming vertex / face count |
| `0x2790` | Vertex deletions |
| `0x2800` | Face deletions |
| `0x2780` | Output faces: three `uint32` output-vertex indices each |
| `0x2840` | `uint32` smoothing groups per output face |
| `0x2830` | `uint32` face flags; **Unknown** |
| `0x3010`-`0x3040` | Further counts; **Unknown** |
| `0x2770`-`0x2776`, `0x2781` | Vertex/face creation and map edits; **Unknown** |

Deletions are positions in the list as it shrinks: deleting `[3, 3]` from five
items removes the original items 3 and 4. Output face `i` is surviving input
face `i`; its material ID and texture coordinates carry over, and `0x2780`
gives its corners in the renumbered vertex list. Output vertex `j` is the
`j`-th surviving input vertex plus its `0x2760` offset.

A later layout with a `0x4000` container (vertex-move records, face creates,
remaps, and changes) occurs in a small number of files and is not decoded.

Evidence: across 338 scenes with matching exporter output, objects whose
Edit Mesh was applied matched the exported geometry exactly for 99% of
objects (2,232 of 2,265), and the UVs of 92.7% of textured ones. Remaining
UV differences concentrate on UVW Maps above an Edit Mesh, which can be
limited to the Edit Mesh's face selection; selections are not yet applied.

BlenderMax applies an Edit Mesh only when the stored incoming counts equal
the actual incoming mesh, the base is an Editable Mesh, and the delta uses no
unknown operation chunk. A `0x2760` array shorter than the vertex count also
occurs; padding it with zero offsets was wrong for most checked objects, so
such data is rejected. The stored counts are compared before any array is
sized from them, because corrupt counts occur.

### Unwrap UVW modifier

**Corroborated** for the 3ds Max 3 Unwrap UVW and UnwrapUVW+ classes, which
share one layout. The modifier itself (not its ModApp) stores a complete copy
of map channel 1:

| Chunk | Content |
|---:|---|
| `0x0100` | `uint32` texture-vertex count `N` |
| `0x0110` | `N` Point3 texture vertices (12 bytes each) |
| `0x0230` | `uint32` face count `F` |
| `0x0240` | `F` faces of three `uint32` texture-vertex indices |
| `0x0260` | UnwrapUVW+ only: one further Point3 per texture vertex; **Unknown** |
| `0x0120` -> `0x2700` | Selection bit array |
| `0x0130`-`0x0250` | Editor settings; **Unknown** |

UnwrapUVW+ also references one Point3 controller per texture vertex. In the
observed files the controller values equal the `0x0110` vertices.

Unwrap face `i` supplies the corners of incoming mesh face `i`, so the data
only applies while the modifiers below it keep the face list. BlenderMax uses
it when `F` equals the base mesh's face count and every face is a triangle,
pairing faces by their original index even when degenerate faces were
dropped on import. Across the ENV-paired scenes, every Unwrap-sourced object
whose positions matched the export (84 objects, 3,051 corners) matched
exactly. An Unwrap with fewer faces than the base mesh usually follows an
Edit Mesh that deleted faces, and is skipped.

### Edit Normals local data

The Edit Normals modifier has one parameter-block reference. Its per-node
ModApp payload distinguishes:

| Chunk | Interpretation |
|---:|---|
| `0x0200` | Named normal-selection sets |
| `0x0240` | Poly-mesh normal specification |
| `0x0250` | Tri-mesh normal specification; mutually exclusive with `0x0240` |
| `0x0260` | Flags; durable bit `0x08` selects tri-mesh mode |

The low three bits of `0x0260` are transient undo state and should not be
treated as durable file semantics. Cached meshes and in-progress editing
buffers are not persisted. The internal normal vectors, specified/explicit
flags, and per-face-corner normal IDs inside `0x0240` and `0x0250` remain
externally encoded, so BlenderMax does not decode them.

### Character Studio Physique weight records

The draft Ryzom corpus report identifies a Physique-specific path:

```text
0x2500 ModApp
  0x2512
    0x2504 instance data
      four header/state children
      0x2506 per mesh vertex
        0x0989 weight record
```

Each `0x0989` record is reported as `4 + 20 * influence_count` bytes:

```text
uint32 influence_count
repeat influence_count times:
    uint32 bone_reference
    float32 bone_local_offset[3]
    float32 weight
```

Records occur in mesh-vertex order. The Physique modifier object's own
reference array is the bone-link table and can contain meaningful holes.
Reported weights are not necessarily normalized; the evaluated result keeps
at most four influences and normalizes their sum. Bone references use
Physique link-tree encodings rather than direct Scene indices, including
different negative rigid and positive cross-link forms.

This is useful evidence for a future Physique decoder, but it does not unblock
native Skin. BlenderMax also lacks the complete Biped-to-armature conversion
needed to apply these weights reliably.

These modifier observations are independently summarized from the draft
[Ryzom Max Geometry Formats](https://wiki.ryzom.dev/drafts/max_geometry_formats)
corpus report reviewed on 2026-11-16. They are treated as external
corroboration rather than a 3ds Max specification and still require
BlenderMax-controlled samples before implementation.

## Geometry semantics corroborated through the SDK

An independent SDK-based exporter corroborates several interpretations without
describing the serialized chunk layout:

- Evaluated geometry is converted to a triangular mesh before export.
- Object-space vertices are multiplied by the object's evaluated transform.
- Texture vertices are selected per face corner, not per geometric vertex.
- Face material IDs select submaterials.
- A negative orientation test,
  `dot(cross(row0, row1), row2) < 0`, indicates mirrored winding and requires
  reversing the face order.
- Normals are transformed without translation and renormalized.

These are scene-semantic observations. They should not be used to assign chunk
IDs unless a controlled serialized sample independently confirms the mapping.

## Legacy parameter blocks

Pre-ParamBlock2 objects, including 3ds Max 3 Standard materials and UVW Map
modifiers, reference an `IParamBlock` (class `0x0008`). Its children are:

```text
0x0001  uint32 parameter count
0x0005  uint16 version
0x0002  one container per parameter:
          0x0003  int32 parameter index
          0x0004  empty marker
          0x0100  float value      | one inline value, or none when a
          0x0101  int32 value      | controller animates the parameter
          0x0102  Point3 / colour  |
```

**Confirmed** for float, int, and colour parameters.

## Material classes

Known combined class IDs:

| Class ID | Material family |
|---:|---|
| `0x0000000000000002` | Standard |
| `0x0000000000000006` | Legacy Standard variant |
| `0x0000000000000200` | Multi/Sub-Object |
| `0x62F74B4C7E73161F` | Arnold Standard Surface |
| `0xDEADC0013D6B1CEC` | Physical Material |
| `0x329B106E27190FF4` | Raytrace |
| `0x4A16365470B05735` | Arch material |
| `0x7034695C37BF3F2F` | V-Ray material |
| `0x11731B4B6066686A` | V-Ray 2-Sided |
| `0x0000000046EE536A` | Shellac |
| `0x448931DD70BE6506` | Corona material |
| `0x8425554E65486584` | Corona Layered |
| `0x871517206912AB89` | Corona Physical |
| `0x44CFDE95BEC41C73` | Corona Skin |
| `0xCA41F54622C05DD6` | Corona Hair |

### 3ds Max 3 Standard material

**Confirmed.** A Standard material (`0x0002`) whose first reference is a legacy
parameter block uses this layout:

| Reference | Content |
|---:|---|
| 0 | Legacy parameter block: 0 ambient, 1 diffuse, 2 specular, 3 glossiness, 4 specular level, 5 self-illumination, 6 opacity, 7 opacity falloff, 8 filter colour, 9 wire size, 10 IOR, 11-16 dynamics and dimming |
| 1 | Texmaps container (class `0x1200`) |

Texmaps stores keyed references `2 * slot` (amount controller) and
`2 * slot + 1` (map) for slots 0 ambient, 1 diffuse, 2 specular, 3 glossiness,
4 specular level, 5 self-illumination, 6 opacity, 7 filter, 8 bump,
9 reflection, 10 refraction, and 11 displacement. Chunk `0x5003` is a bitmask
of enabled slots; in a 10,619-container sample it matched the populated slots
exactly except for one disabled map. `0x5100`-`0x510B` hold per-slot amounts.
A legacy Bitmap (`0x0240`) stores its path in `0x5010 -> 0x1230`.

Property-block layouts for renderer materials are only partially understood.
The importer extracts a common approximation: base color, specular color,
roughness/shininess, metallic, opacity, emission, refraction/IOR, and selected
texture paths. The resulting Principled BSDF is not evidence of exact renderer
semantics.

## Multi/Sub-Object materials

The parent material's `0x4002` child can contain the submaterial count as a
little-endian `uint32`. References may begin with a parameter block whose class
ID is `0x82`, followed by submaterial references.

Sparse material-ID tables have been observed in parameter chunks `0x000A` and
`0x000E`:

```text
variant  header bytes before count
0x000A   11
0x000E   15
```

At byte zero, the first two `uint16` values include a parameter type. Type
`0x0801` identifies the table handled by BlenderMax. At the variant-specific
header offset:

```text
uint32 stored_count
repeat stored_count times:
    uint8  unknown
    int32  one_based_material_id
```

The importer subtracts one from each positive ID and preserves holes by
creating placeholder slots when needed. Face material IDs are then remapped
through this sparse table rather than assumed to equal the Blender slot index.

Without a sparse table, IDs are dense and face IDs beyond the submaterial
count wrap around: ID `n` uses submaterial `n mod count`.

## Texture paths

Texture paths can be inline strings, linked metadata, or properties reachable
through reference graphs. The importer has observed:

- Inline path strings in `0x1230`.
- Linked metadata under `0x1260`.
- Renderer property graphs containing a UTF-16 property named `filename`.

Absolute source paths usually refer to another machine. The trailing
directories are often still meaningful, because source depots tend to move as
a whole: the resolver tries the stored path's directory tails under each
ancestor of the opened scene, longest tail first, before falling back to
basename searches.

## Coincident faces

Scenes may contain coincident faces with opposite winding, typically to make
walls double-sided. Blender's `Mesh.validate()` deletes such duplicates, which
also breaks the alignment of per-face smoothing, material, and UV arrays. The
importer instead removes only faces with out-of-range or repeated vertex
indices, filters the per-face arrays with the same mask, and keeps coincident
faces.

## Known gaps

- Native Skin weights, bone reconstruction, and modifier-stack evaluation.
- Character Studio Physique weights and link-tree/Biped reconstruction.
- Authored explicit normals and Edit Normals modifier data.
- Smoothing groups outside the confirmed Editable Mesh `0x0912` and Editable
  Poly `0x011A` variants.
- N-gon boundary and hidden-edge semantics in every Editable Poly version.
- Multiple UV channels outside the currently observed mapping families.
- Edit Mesh vertex/face creation, map-channel edits, the `0x4000` delta
  layout, and Edit Mesh on primitive or Editable Poly bases.
- Sub-object selections that limit a UVW Map, and Unwrap UVW data on stacks
  whose face list changes below the Unwrap without being evaluated.
- Exact renderer-material parity.
- Procedural textures and renderer plug-in objects.
- Robust Boolean operand/reference variants.
