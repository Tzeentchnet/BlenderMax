# BlenderMax

BlenderMax is a native Autodesk 3ds Max (`.max`) importer for Blender 5.2 LTS.
It is a modernized fork of
[nrgsille76/io_scene_max](https://github.com/nrgsille76/io_scene_max), with
NumPy-accelerated geometry decoding, Blender 5.2 layered actions, improved
texture discovery, and a repeatable headless corpus runner.

## Highlights

- Blender 5.2 extension package and file-handler integration.
- Native OLE/MAX parsing without requiring an Autodesk 3ds Max installation.
- NumPy decoding for vertex, face, triangle, and UV buffers.
- Blender bulk-data APIs for mesh positions, loops, polygons, UVs, material
  indices, and animation key coordinates.
- Editable Mesh, Editable Poly, common primitives, object hierarchy, pivots,
  UV maps, and per-face material IDs.
- MAX row-vector transforms converted to Blender's column-vector convention,
  including stored quaternion signs and arbitrary scale-axis matrices.
- Basic camera, camera-target, omni-light, and direct-light reconstruction,
  including camera look-at constraints.
- Standard, legacy, Physical, Arnold Standard Surface, Arch, Raytrace, V-Ray,
  and Corona material families where their native chunks are understood.
- External texture lookup from the MAX directory, an optional texture root,
  and nearby `materialsrc`, `materials`, `textures`, or `maps` directories.
- Standard PRS animation for Linear Position/Rotation/Scale, legacy and modern
  Bezier Position/Scale, and TCB Position/Rotation/Scale controllers.
- Multi/Sub-Object materials preserve reference holes and explicit sparse
  material-ID tables.
- One Blender 5.2 layered/slotted Action per imported MAX file, with one slot
  per animated object.
- Source-key or baked-per-frame animation modes.
- Deterministic headless import reports and saved `.blend` files for review.

## Current limitations

The `.max` format is a proprietary serialization of 3ds Max plug-in objects.
Support is necessarily class- and version-specific.

- Character Studio Biped controllers are detected and reported, but their
  proprietary monolithic animation stream is not decoded yet.
- Skin weights, modifier stacks, procedural animation, general constraints,
  morphs, splines, particles, renderer proxies, and renderer-specific
  camera/light parameters are not yet reconstructed.
- Unsupported object classes are skipped with a console diagnostic.
- Renderer-specific materials are converted to an approximate Principled BSDF
  graph; exact renderer parity is not possible.
- Animated scale-axis shear is decomposed to Blender location, quaternion, and
  scale channels; static scale-axis matrices retain the full affine transform.

## Installation

1. Build the extension:

   ```powershell
   & "D:\Blender Foundation\Blender 5.2\blender.exe" -c extension build `
     --source-dir="D:\Github\BlenderMax\source" `
     --output-dir="D:\Github\BlenderMax\dist"
   ```

2. In Blender 5.2, open **Edit > Preferences > Get Extensions**, use
   **Install from Disk**, and select `dist\blender_max-2.0.0.zip`.
3. Import a file with **File > Import > BlenderMax / Autodesk MAX (.max)**.

The Python operator is `bpy.ops.import_scene.blender_max`.

## Import options

- **Image Search** searches recursively for texture basenames.
- **Texture Directory** supplies an explicit texture root.
- **Object Filter** controls materials, UV maps, primitives, empties, and
  armature helpers.
- **Apply Matrix** applies MAX object/pivot transforms.
- **Transform Animation** enables supported PRS animation.
- **Sampling**
  - **Bake Every Frame** gives reliable quaternion playback.
  - **Source Keys** creates smaller actions at native key times.
- **Use Scene FPS** reads the frame rate and active range from the MAX Config
  stream.
- **Source FPS** is the fallback when scene timing is unavailable or disabled;
  it converts 3ds Max ticks (4,800 per second) to Blender frames.
- **Frame Offset** shifts imported animation in the Blender timeline.

## HL2 corpus regression testing

The corpus runner defaults to:

- Blender: `D:\Blender Foundation\Blender 5.2\blender.exe`
- Corpus: `F:\ModelArchive\hl2\hl2`
- Results: `E:\tests\BlenderMax\corpus`

Run the checked-in smoke manifest:

```powershell
python tools\run_corpus.py `
  --manifest tests\corpus_smoke.txt `
  --output E:\tests\BlenderMax\smoke
```

Run a deterministic size- and animation-stratified sample:

```powershell
python tools\run_corpus.py --limit 25
```

Run all MAX files:

```powershell
python tools\run_corpus.py --all --skip-existing
```

Each import produces:

- `blend\...`: the imported `.blend` for visual review.
- `reports\...`: object, geometry, material, image, action, and timing metrics.
- `logs\...`: the complete Blender console log.
- `summary.json`: aggregate pass/fail/timeout results.

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
  dist\blender_max-2.0.0.zip
```

`tools\inspect_max.py` can dump scene-node and controller evidence for format
research:

```powershell
& "D:\Blender Foundation\Blender 5.2\blender.exe" `
  --background --factory-startup `
  --python tools\inspect_max.py -- input.max --output controllers.json
```

## Credits and license

BlenderMax remains based on work by Sebastian Schrand, Jens M. Plonka, and
Philippe Lagadec, including code derived from FreeCAD Importer3D and the
`olefile` parser.

See [LICENSE](LICENSE). Contributions must remain compatible with the
project's GPL license.
