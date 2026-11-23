# BlenderMax

BlenderMax is a native Autodesk 3ds Max (`.max`) importer for Blender 5.2 LTS.
It is a modernized fork of
[nrgsille76/io_scene_max](https://github.com/nrgsille76/io_scene_max), with
NumPy-accelerated geometry decoding, Blender 5.2 layered actions, improved
texture discovery, and researcher-facing format documentation.

## Features

### File and scene decoding

- Native OLE Compound File parsing without an Autodesk 3ds Max installation.
- Support for `ClassDirectory`, `ClassDirectory2`, and `ClassDirectory3`
  scene variants.
- Editable Mesh, Editable Poly, PolyMeshObject, Boolean, and Shell geometry.
- Box, sphere, cylinder, torus, cone, and plane primitives.
- Object names, hierarchy, pivots, object offsets, UV maps, and per-face
  material IDs.
- Editable Mesh and Editable Poly smoothing masks, preserved as the
  `max_smoothing_group` face attribute and reconstructed as Blender smooth
  faces, sharp edges, and split normals.
- Camera, camera-target, omni-light, and direct-light reconstruction,
  including camera look-at constraints.

### Performance and transforms

- NumPy decoding for vertex, face, triangle, UV, material-index, and animation
  buffers.
- Blender bulk-data APIs for mesh and keyframe creation.
- MAX row-vector transforms converted to Blender's column-vector convention.
- Quaternion component/sign conversion and arbitrary static scale-axis
  matrices.

### Materials and textures

- Standard, legacy Standard, Physical, Arnold Standard Surface, Arch,
  Raytrace, V-Ray, and Corona material families where their native chunks are
  understood.
- Multi/Sub-Object materials with preserved reference holes and sparse
  material-ID tables.
- External texture lookup beside the MAX file, from an explicit texture root,
  and from nearby `materialsrc`, `materials`, `textures`, or `maps`
  directories.
- Base-color, roughness, specular, opacity, normal, emission, metallic, and
  refraction inputs mapped to Blender's Principled BSDF where available.

### Animation and Blender integration

- Linear Position/Rotation/Scale, legacy and modern Bezier Position/Scale, and
  TCB Position/Rotation/Scale key layouts, including Bezier tangent and TCB
  parameter evaluation during baked sampling.
- Scene FPS and active-frame-range decoding from the MAX `Config` stream.
- Source-key and baked-per-frame sampling modes.
- One Blender 5.2 layered/slotted Action per imported MAX file, with one slot
  per animated object.
- Blender 5.2 extension packaging, file-handler integration, and drag-and-drop
  support.

## Current limitations

The `.max` format is a proprietary serialization of 3ds Max plug-in objects.
Support is necessarily class- and version-specific.

### Objects and geometry

- Unsupported or unknown plug-in object classes are skipped with a console
  diagnostic.
- Modifier stacks are unwrapped to their base geometry but not evaluated; the
  discarded modifiers are reported in the console.
- Native Skin and Character Studio Physique weights, morph targets, authored
  explicit vertex normals, splines, particles, and renderer proxies are not
  yet reconstructed.
- Smoothing-derived normals are supported for the confirmed Editable Mesh and
  Editable Poly layouts. Authored normal specifications and the Edit Normals
  modifier are not decoded.
- Character Studio Biped objects can appear as hierarchy helpers, but they are
  not converted into a complete Blender armature and skin.

### Animation

- Character Studio Biped's proprietary monolithic animation stream is
  detected but not decoded.
- General constraints, procedural controllers, and unsupported controller
  classes are not reconstructed.
- **Source Keys** writes only native key times with linear Blender
  interpolation; use **Bake Every Frame** to reproduce evaluated Bezier and
  TCB curve shapes.
- Weighted Bezier handle lengths and TCB rotation metadata for
  multi-revolution segments are not evaluated yet.
- Animated scale-axis shear is decomposed into Blender location, quaternion,
  and scale channels. Static scale-axis matrices retain the full affine
  transform.

### Materials and rendering

- Renderer-specific materials are approximated with a Principled BSDF graph;
  exact V-Ray, Corona, Arnold, or legacy renderer parity is not possible.
- Procedural textures, complex layered shader behavior, and renderer-specific
  camera or light parameters are not reconstructed.
- Missing external image files remain unresolved and must be relinked
  manually or supplied through the texture-directory option.

## Format research

Researcher-facing notes on the observed MAX container, chunk grammar, class
indirection, scene data, geometry, materials, transforms, and animation are
indexed in [docs/README.md](docs/README.md).

## Installation

1. Build the extension:

   ```powershell
   & "D:\Blender Foundation\Blender 5.2\blender.exe" -c extension build `
     --source-dir="D:\Github\BlenderMax\source" `
     --output-dir="D:\Github\BlenderMax\dist"
   ```

2. In Blender 5.2, open **Edit > Preferences > Get Extensions**, use
   **Install from Disk**, and select `dist\blender_max-2.0.2.zip`.
3. Import a file with **File > Import > BlenderMax / Autodesk MAX (.max)**.

The Python operator is `bpy.ops.import_scene.blender_max`.

## Import options

- **Image Search** searches recursively for texture basenames.
- **Texture Directory** supplies an explicit texture root.
- **Check Import Folder First** (add-on preference, on by default) looks for
  each external texture by name in the folder the `.max` file was imported
  from before trying the stored path or a recursive search. An explicitly set
  **Texture Directory** (including its subfolders when Image Search is on)
  always wins. Useful for source depots that keep textures beside the scenes.
- **Object Filter** controls materials, UV maps, primitives, empties, and
  armature helpers.
- **Apply Matrix** applies MAX object/pivot transforms.
- **Transform Animation** enables supported PRS animation.
- **Sampling**
  - **Bake Every Frame** evaluates supported Bezier and TCB curves and gives
    reliable quaternion playback.
  - **Source Keys** creates smaller actions at native key times, with linear
    interpolation between the written keys.
- **Use Scene FPS** reads the frame rate and active range from the MAX Config
  stream.
- **Source FPS** is the fallback when scene timing is unavailable or disabled;
  it converts 3ds Max ticks (4,800 per second) to Blender frames.
- **Frame Offset** shifts imported animation in the Blender timeline.

## Development checks

```powershell
# Controller layouts, transforms, materials, timing, and layered-action tests
& "D:\Blender Foundation\Blender 5.2\blender.exe" `
  --background --factory-startup `
  --python tests\test_max_animation.py

# Build and validate the extension
& "D:\Blender Foundation\Blender 5.2\blender.exe" -c extension build `
  --source-dir=source --output-dir=dist
& "D:\Blender Foundation\Blender 5.2\blender.exe" -c extension validate `
  dist\blender_max-2.0.2.zip
```

`tools\inspect_max.py` can dump bounded scene-node, geometry, modifier, and
controller evidence for format research:

```powershell
& "D:\Blender Foundation\Blender 5.2\blender.exe" `
  --background --factory-startup `
  --python tools\inspect_max.py -- input.max --output scene.inspect.json
```

## Credits and license

BlenderMax remains based on work by Sebastian Schrand, Jens M. Plonka, and
Philippe Lagadec, including code derived from FreeCAD Importer3D and the
`olefile` parser.

See [LICENSE](LICENSE). The project is licensed under GPL-3.0-or-later.
Contributions must remain compatible with this license.
