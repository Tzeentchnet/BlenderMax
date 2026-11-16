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
`0x405F22F529263A68`. Its `0x2034` reference array stores modifiers in stack
order followed by the base object. A parallel orphaned `0x2500` ModApp
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

BlenderMax currently imports the final base-object reference and emits a
console diagnostic for every discarded modifier. It does not interpret
native Skin's `0x2512` payload or create speculative vertex groups. Native
Skin and Character Studio Physique use different formats and must not share a
decoder merely because both represent deformation weights.

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

## Texture paths

Texture paths can be inline strings, linked metadata, or properties reachable
through reference graphs. The importer has observed:

- Inline path strings in `0x1230`.
- Linked metadata under `0x1260`.
- Renderer property graphs containing a UTF-16 property named `filename`.

Only the basename should be considered portable. Absolute source paths often
refer to another machine and are not reliable evidence of the asset's intended
directory layout.

## Known gaps

- Native Skin weights, bone reconstruction, and modifier-stack evaluation.
- Character Studio Physique weights and link-tree/Biped reconstruction.
- Authored explicit normals and Edit Normals modifier data.
- Smoothing groups outside the confirmed Editable Mesh `0x0912` and Editable
  Poly `0x011A` variants.
- N-gon boundary and hidden-edge semantics in every Editable Poly version.
- Multiple UV channels outside the currently observed mapping families.
- Exact renderer-material parity.
- Procedural textures and renderer plug-in objects.
- Robust Boolean operand/reference variants.
