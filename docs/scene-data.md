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

### Faces and material IDs: `0x0912`

The payload begins with a `uint32` count followed by 20-byte records:

```text
offset  size  type       current meaning
0       12    uint32[3]  vertex indices
12      4     uint32     edge/visibility data
16      2     uint16     face flags
18      2     uint16     face material ID
```

Vertex indices and material IDs are **confirmed**. The detailed bit meanings
of the edge and flag fields are not yet documented.

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
| `0x011A` | Variable point/topology records with flags and material group | Tentative |
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
    uint32 flag3
if flags & 0x20:
    uint32 extra_bits[2 * (point_index_count - 3)]
```

The conditional layout is observed, but the semantic names of the flag fields
are placeholders. `material_group` is used as a face material ID.

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

- Skin weights and modifier-stack serialization.
- Explicit vertex normals and smoothing-group reconstruction for all variants.
- N-gon boundary and hidden-edge semantics in every Editable Poly version.
- Multiple UV channels outside the currently observed mapping families.
- Exact renderer-material parity.
- Procedural textures and renderer plug-in objects.
- Robust Boolean operand/reference variants.
