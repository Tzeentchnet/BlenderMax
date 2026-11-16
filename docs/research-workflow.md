# Research workflow

## Inspecting a file

Run the bounded inspector through Blender so `bpy` and `mathutils` are
available:

```powershell
blender --background --factory-startup `
  --python tools\inspect_max.py -- `
  sample.max `
  --output sample.inspect.json `
  --max-depth 6
```

The JSON report includes:

- The compound-file major-version field currently named `dll_version`.
- Number of top-level Scene chunks.
- Scene nodes resolved to superclass ID `0x1`.
- Node name, parent, class ID, mesh class, material class, and layer class.
- Geometry, modifier, and transform-controller children and references.
- Primitive data summaries.
- SHA-256 and at most the first 128 bytes of an opaque byte payload.
- Derived-object modifier references paired positionally with orphaned
  `0x2500` ModApp slots.
- Cycle markers when a reference graph points back to an object already seen.

Increasing `--max-depth` can grow reports quickly. Start at two or three and
increase it only around a controller or object of interest.

An empty `nodes` array does not prove that a source scene is empty. First
verify that the correct class-directory stream was located and decoded. A
failed class table prevents Scene chunk indices from resolving to node
superclasses.

## Recommended experiment design

Use synthetic scenes whenever possible:

1. Start from an empty scene.
2. Add exactly one object or controller.
3. Save a baseline.
4. Change one property.
5. Save a second file.
6. Compare stream names, chunk trees, payload lengths, and changed bytes.

Useful single-variable experiments include:

- One vertex moved along one axis.
- One face assigned a different material ID.
- One UV corner moved.
- One parent relationship added.
- One pivot translation or rotation changed.
- One key added at a known tick.
- One controller changed from Linear to TCB or Bezier.
- One Multi/Sub-Object material ID made sparse.
- Two adjacent faces assigned overlapping, disjoint, and zero smoothing masks.
- One face-corner normal made explicit and rotated to a known vector.
- One vertex weighted 100% to one bone, then 75/25 to two bones.

For version research, create the same minimal scene in each available release.
Do not compare unrelated production files and attribute every difference to
the application version.

## Record an evidence packet

For every proposed layout, record:

```text
File hash:
Creating application/release:
Plug-ins required:
CFB stream name and size:
Class-directory variant:
Combined class ID:
Superclass ID:
Human-readable class name:
Scene child index:
Reference path from node:
Chunk path:
Payload length:
Hexadecimal bytes:
Controlled property value:
Hypothesis:
Alternative explanations:
Confidence:
```

Hashes and minimal byte excerpts make an observation reproducible without
redistributing a proprietary sample.

## Class IDs before chunk meanings

Do not assign semantics from a Scene chunk's 16-bit type alone:

1. Read the active class-directory variant.
2. Use the Scene chunk type as a class-table index.
3. Read class-directory child `0x2060`.
4. Compare the combined class ID and superclass ID.
5. Only then apply a class-specific payload decoder.

The same small Scene chunk type can refer to a different class in another
file because the class table is per-file.

## Reference-graph discipline

When following `0x2034` or `0x2035`:

- Preserve reference order.
- Preserve missing entries and holes.
- Bounds-check Scene indices.
- Record keyed-reference numbers instead of renumbering them.
- Track visited object identities to stop cycles.
- Do not assume every reference points to a node; controllers, parameter
  blocks, materials, and plug-in objects share the same graph.

## Adding a decoder

Before implementing a new class or chunk:

1. Collect at least one positive sample and, where possible, a one-property
   comparison.
2. Identify the class ID, superclass ID, reference slot, and payload boundary.
3. Write the narrowest parser that explains the evidence.
4. Validate counts and record sizes before allocating arrays.
5. Keep unrecognized trailing bytes available for later study.
6. Add a focused synthetic test for the binary layout.
7. Verify that malformed lengths fail explicitly.
8. Run an import and check semantic output, not just parser completion.

The executable layout examples in
[`tests/test_max_animation.py`](../tests/test_max_animation.py) show the
preferred pattern: construct exact bytes, decode them, and assert values and
coordinate conversion.

## Comparing geometry

For a geometry decoder, verify at least:

- Vertex count and bounds.
- Face count and loop count.
- Every vertex index is in range.
- Winding on both ordinary and mirrored transforms.
- Per-face material IDs, including sparse Multi/Sub IDs.
- UV channel count and per-loop coordinates.
- Per-face smoothing masks, smooth polygons, sharp shared edges, and resulting
  corner normals.
- Bone identities, per-vertex influence counts, normalized weights, and
  unweighted vertices when testing a skin decoder.
- Object pivot and parent transform.

A scene that opens without an exception is not sufficient evidence. A wrong
class directory or a skipped object can otherwise look like a successful
empty import.

## Comparing animation

For an animation decoder, verify:

- Tick values before frame conversion.
- Scene FPS and active range.
- Static pose at tick zero or the nearest source anchor.
- Controller wrapper/list unwrapping.
- Quaternion component order and sign convention.
- Quaternion continuity across 180-degree boundaries.
- Scale-axis orientation.
- Duplicate-time behavior.
- Curve values between keys, not only at keys.

Describe curve reconstruction as exact only for corroborated fields. The
current baked evaluator covers Bezier tangent slopes and TCB
tension/continuity/bias/ease, but not weighted Bezier handle lengths or TCB
multi-revolution rotation metadata.

## Handling unknown data

Prefer an explicit unknown over a guessed value:

- Keep the payload as bytes.
- Report the class ID and payload length.
- Emit a bounded hexadecimal prefix.
- Do not reinterpret malformed data as zeros.
- Do not catch broad exceptions and continue with success-shaped output.
- Distinguish unsupported classes from corrupt files.

This discipline is especially important for plug-in classes whose parameter
blocks can change independently of the 3ds Max release.

## Evidence required for Skin and explicit normals

Native Skin (`0x000156660095C6A3`) and Character Studio Physique are distinct
modifier classes with distinct local-data formats. Physique is class
`(0x100, 0)` with superclass `0x810`; its documented `0x2506 -> 0x0989`
records must not be used to infer native Skin. A minimum authorized Skin
comparison set is:

1. A mesh and bone hierarchy without Skin.
2. The same mesh with one vertex weighted 100% to one bone.
3. The same mesh with one vertex weighted 75/25 to two bones.

Keep topology, names, bind transforms, modifier settings, and save version
identical. Record the derived-wrapper references and each corresponding
`0x2500` ModApp container, especially the `0x2512` subtree.

For explicit normals, use a minimal triangle or cube:

1. Save a baseline with default normals.
2. Add Edit Normals without changing a direction.
3. Rotate exactly one face-corner normal to a known unit vector and save
   again.

Record whether the change appears in the Edit Normals ModApp's poly-mesh
`0x0240` or tri-mesh `0x0250` payload. Chunk `0x0260` bit `0x08` identifies
tri-mesh mode; its lower three bits are transient undo markers. A valid
decoder must establish normal-vector storage, explicit/specified flags,
per-face-corner normal IDs, and version boundaries before calling Blender's
custom split-normal API.

## Legal and privacy considerations

- Use files you are authorized to inspect.
- Do not commit proprietary `.max` samples.
- Do not copy third-party source into these notes.
- Do not publish local asset paths, user names, or confidential project names.
- Prefer synthetic scenes, hashes, field tables, and independently written
  descriptions.
