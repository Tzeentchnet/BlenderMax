# Transforms and animation

## Matrix convention

3ds Max SDK `Matrix3` values use row-vector composition. SDK behavior
corroborates these relationships:

```text
local = child_world * inverse(parent_world)
object_offset = object_world * inverse(node_world)
```

The three upper rows carry basis vectors and the fourth row carries
translation. Blender uses column-vector `4x4` matrices, so BlenderMax builds
equivalent transforms in Blender order:

```text
Translation @ Rotation @ Scale
```

This is a semantic conversion, not a direct byte-for-byte matrix copy.

## Quaternion conversion

Serialized transform quaternions used by the supported controllers are stored
as:

```text
x, y, z, w
```

For Blender's convention, BlenderMax uses:

```text
w, -x, -y, -z
```

The quaternion is normalized. A near-zero quaternion becomes the identity.
The sign change is covered by an executable regression test and is required to
match the observed coordinate conversion.

## Scale-axis values

Scale keys and static scale controllers can provide seven floats:

```text
sx, sy, sz, axis_x, axis_y, axis_z, axis_w
```

The first three values form a diagonal scale matrix `S`. The last four form a
normalized scale-axis quaternion whose matrix is `A`. BlenderMax reconstructs:

```text
A^T @ S @ A
```

This preserves static arbitrary scale axes and shear-like affine effects. When
animated samples are written to Blender location/quaternion/scale channels,
the resulting matrix must be decomposed, so animated shear cannot be retained
exactly.

## Node pivot/object-offset chunks

The node-local chunks below are applied to mesh data before the node transform:

| Chunk | Value |
|---:|---|
| `0x096A` | Pivot/object-offset translation |
| `0x096B` | Pivot/object-offset quaternion (`x y z w`) |
| `0x096C` | Pivot/object-offset scale and optional scale axis |

The resulting mesh-data transform is:

```text
Translation(pivot) @ Rotation(pivot_quaternion) @ Scale(pivot_scale_axis)
```

Keeping this separate from the node transform is necessary to preserve object
offsets and parent hierarchy behavior.

## Transform controller classes

Known combined class IDs:

| Class ID | Controller | Status |
|---:|---|---|
| `0x0000000000002005` | Position/Rotation/Scale | Confirmed |
| `0x0000000000002006` | Look At | Confirmed in part |
| `0x000000004B4B1002` | Position List | Confirmed first active reference |
| `0x000000004B4B1003` | Rotation List | Confirmed first active reference |
| `0xFFEE238A118F7E02` | Position Wire / Position XYZ | Confirmed wrapper |
| `0x3A90416731381913` | Rotation Wire | Confirmed wrapper |
| `0xFEEE238B118F7C01` | Scale Wire / Scale XYZ | Confirmed wrapper |
| `0x0000000000009154` | Biped slave controller | Static transform only |
| `0x0000000000009156` | Biped vertical/horizontal/turn | Detected, animation unknown |
| `0x0000000000003011` | Biped footsteps | Detected, animation unknown |

For a PRS controller, positional references are:

| Reference | Meaning |
|---:|---|
| `0` | Position controller |
| `1` | Rotation controller |
| `2` | Scale controller |

For the observed Look At controller:

| Reference | Meaning |
|---:|---|
| `0` | Look-at target |
| `1` | Position controller |
| `3` | Scale controller |

Rotation is derived from the target constraint rather than a normal rotation
track.

## Static component controllers

The current static decoder recognizes:

| Component | Class ID | Interpretation |
|---|---:|---|
| Position | `0x2008` | Bezier Position; value in `0x2503` |
| Position | `0x442312` | TCB Position; value in `0x2503` |
| Rotation | `0x2012` | Euler XYZ |
| Rotation | `0x442313` | TCB Rotation; quaternion in `0x2504` |
| Scale | `0x2010` | Bezier Scale |
| Scale | `0x442315` | TCB Scale/Zoom |
| Scale | `0x2004` | Linear Scale |

List and wire controllers are unwrapped through their references before the
component value is read.

## Animation key record header

All currently supported key arrays use a fixed record size. Every record starts
with:

```text
offset  size  type       meaning
0       4     int32      time in MAX ticks
4       4     uint32     key flags
8       ...              value
```

The key flags and corroborated interpolation fields are decoded with the key
value. For Bezier keys, outgoing tangent-type bits `10..12` select a step
segment when their value is `2`.

### Supported key layouts

| Chunk | Track | Record bytes | Value at offset 8 | Curve fields |
|---:|---|---:|---|---|
| `0x2513` | Linear Position | 20 | `float32[3]` | None |
| `0x2521` | TCB Position | 64 | `float32[3]` | TCB at 20 |
| `0x2524` | Legacy Bezier Position | 56 | `float32[3]` | In/out tangents at 20/32 |
| `0x2526` | Bezier Position | 80 | `float32[3]` | In/out tangents at 20/32 |
| `0x2514` | Linear Rotation | 24 | `float32[4]` quaternion | None |
| `0x2522` | TCB Rotation | 92 | `float32[4]` quaternion | TCB at 24 |
| `0x2515` | Linear Scale | 36 | `float32[7]` scale/axis | None |
| `0x2523` | TCB Scale | 112 | `float32[7]` scale/axis | TCB at 36 |
| `0x2519` | Legacy Bezier Scale | 92 | `float32[7]` scale/axis | In/out tangents at 36/64 |
| `0x2528` | Bezier Scale | 148 | `float32[7]` scale/axis | In/out tangents at 36/64 |

Every TCB block is five `float32` values in this order:

```text
tension, continuity, bias, ease-in, ease-out
```

Bezier position tangents are value units per MAX tick. Bezier scale tangents
use value units per second. The evaluator converts both to cubic control
points for the segment duration. Remaining record fields include caches and,
for some Bezier variants, weighted-handle data whose semantics are not yet
fully reconstructed.

A payload must be an exact multiple of its record size. Rejecting malformed
lengths prevents one incorrect layout guess from shifting every later key.

Records are stable-sorted by tick. When duplicate ticks occur, the last record
at that tick is retained.

## Scene timing

MAX time uses 4800 ticks per second. BlenderMax has confirmed a `Config`
container `0x20B0` with little-endian signed 32-bit children:

| Chunk | Meaning |
|---:|---|
| `0x0010` | Frames per second |
| `0x0050` | Active-range start tick |
| `0x0060` | Active-range end tick |
| `0x0070` | Current tick |

Frame conversion is:

```text
ticks_per_frame = 4800 / fps
frame = tick / ticks_per_frame + frame_offset
```

Implausible FPS values and reversed ranges are rejected.

## Current evaluation model

BlenderMax currently evaluates:

- Linear position and scale by component-wise linear interpolation.
- Bezier position and scale with cubic control points reconstructed from the
  stored incoming and outgoing tangent slopes. Step outgoing tangents hold the
  preceding value.
- TCB position and scale with non-uniform Hermite tangents derived from
  tension, continuity, and bias, plus the stored ease-in/ease-out time warp.
- Linear rotation with quaternion spherical interpolation and TCB rotation
  with spherical quadrangle interpolation using TCB-derived controls.
- Missing components from the static source transform.
- Source-key sampling at the union of animated component key times, or baked
  sampling at every source frame.

The transform at each sample is rebased from the sampled source anchor into
the imported object's target anchor:

```text
target = target_anchor * inverse(sampled_source_anchor) * source_sample
```

Successive output quaternions are sign-adjusted when their dot product is
negative, avoiding discontinuities caused by equivalent `q` and `-q`
representations.

### Fidelity limitations

**Bake Every Frame** evaluates the supported Bezier and TCB fields before
writing Blender keys. **Source Keys** intentionally writes only the native key
times and uses linear Blender interpolation, so it preserves values and timing
but not nonlinear segment shapes.

Weighted Bezier handle-length fields are not applied. TCB rotation currently
uses the absolute key quaternions; the stored relative angle-axis metadata
needed to retain explicit multi-revolution segments is not evaluated. For
animated scale, tangent/TCB interpolation applies to the three scale values;
the scale-axis quaternion follows the eased segment parameter before Blender
matrix decomposition.

## Character Studio Biped

Biped object and controller classes are detectable, and a static transform can
sometimes be recovered through referenced subcontrollers. Full animation is
not decoded.

The main obstacle is a proprietary monolithic subanimation stream rather than
the ordinary PRS key arrays documented above. It must not be scanned for
ordinary key chunk IDs without structural evidence; coincidental byte patterns
would produce plausible but invalid motion.

## Research priorities

1. Decode and validate weighted Bezier handle lengths.
2. Preserve TCB rotation relative angle-axis and multi-revolution segments.
3. Establish active-controller selection rules for list controllers.
4. Map additional constraint classes and their target/reference slots.
5. Characterize Biped subanimation framing with controlled synthetic files.
6. Preserve animated affine shear through a matrix-capable representation or
   a documented approximation.
