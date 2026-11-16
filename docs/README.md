# 3ds Max format research notes

These documents describe the `.max` structures observed while developing
BlenderMax. They are intended to help format researchers reproduce findings,
compare files from different 3ds Max generations, and add narrowly verified
decoders.

This is not an Autodesk specification. A `.max` file serializes a graph of
3ds Max and plug-in objects, so layouts can vary by release, object class, and
installed plug-in. Treat every table here as evidence about known variants,
not a promise that all files use the same layout.

## Evidence labels

- **Confirmed**: implemented in the importer and covered by a focused test or
  repeatedly observed in imported files.
- **Corroborated**: agrees with both serialized data and behavior observed
  through the 3ds Max SDK.
- **Tentative**: sufficient for a known variant, but field meaning or version
  coverage remains incomplete.
- **Unknown**: preserved or skipped because its semantics have not been
  established.

## Documents

- [File structure and chunks](file-structure.md) describes the Compound File
  container, standard streams, chunk framing, class directories, and reference
  encoding.
- [Scene data, geometry, and materials](scene-data.md) describes node
  references, known object class IDs, Editable Mesh and Editable Poly payloads,
  UV data, and material assignment.
- [Transforms and animation](animation.md) describes MAX matrix conventions,
  pivot data, PRS controllers, key record layouts, scene timing, and current
  Character Studio limitations.
- [Research workflow](research-workflow.md) describes how to inspect a file,
  design controlled comparison scenes, record evidence, and contribute a new
  decoder safely.

## High-level model

The currently observed decoding path is:

1. Open the file as an OLE Compound File Binary container.
2. Locate named streams such as `DllDirectory`, a class-directory variant,
   `Config`, and `Scene`.
3. Decode each stream as a tree of signed-size chunks.
4. Resolve a Scene chunk's small type number through the class directory.
5. Resolve positional or keyed references into the Scene root's child array.
6. Interpret the referenced class-specific payload.

The class-directory step is essential. In the `Scene` stream, a chunk type is
often an index into a per-file class table, not a globally stable object class
ID.

## Primary implementation references

- [`source/import_max.py`](../source/import_max.py) contains the container,
  chunk, class, scene, geometry, and material decoders.
- [`source/max_animation.py`](../source/max_animation.py) contains the
  controller key layouts and transform sampling logic.
- [`tools/inspect_max.py`](../tools/inspect_max.py) emits bounded JSON evidence
  for scene nodes and controller graphs.
- [`tests/test_max_animation.py`](../tests/test_max_animation.py) contains
  executable examples of confirmed record layouts and transform conversions.

## Scope and responsible use

The notes contain structural observations and independently derived field
interpretations. Do not add proprietary sample files, confidential asset
names, or copied third-party source to this repository. Record hashes,
minimal synthetic reproductions, and bounded hexadecimal evidence instead.
