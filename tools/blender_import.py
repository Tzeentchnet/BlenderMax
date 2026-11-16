# SPDX-License-Identifier: GPL-2.0-or-later

"""Import one MAX file in Blender and emit a machine-readable report."""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import traceback
from pathlib import Path

import bpy


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

import source  # noqa: E402


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--texture-directory", type=Path)
    parser.add_argument("--animation-fps", type=float, default=30.0)
    parser.add_argument("--no-scene-fps", action="store_true")
    parser.add_argument(
        "--animation-sampling",
        choices=("BAKE", "KEYS"),
        default="BAKE",
    )
    parser.add_argument("--no-animation", action="store_true")
    parser.add_argument("--no-image-search", action="store_true")
    return parser.parse_args(arguments)


def action_stats() -> list[dict]:
    actions = []
    for action in bpy.data.actions:
        slots = []
        for slot in action.slots:
            fcurves = 0
            keyframes = 0
            for layer in action.layers:
                for strip in layer.strips:
                    channelbag = strip.channelbag(slot, ensure=False)
                    if channelbag is None:
                        continue
                    fcurves += len(channelbag.fcurves)
                    keyframes += sum(
                        len(fcurve.keyframe_points)
                        for fcurve in channelbag.fcurves
                    )
            slots.append(
                {
                    "name": slot.name_display,
                    "fcurves": fcurves,
                    "keyframes": keyframes,
                }
            )
        actions.append(
            {
                "name": action.name,
                "frame_range": list(action.frame_range),
                "slots": slots,
            }
        )
    return actions


def scene_stats(elapsed_seconds: float) -> dict:
    objects_by_type = {}
    finite_transforms = True
    for obj in bpy.data.objects:
        objects_by_type[obj.type] = objects_by_type.get(obj.type, 0) + 1
        finite_transforms &= all(
            math.isfinite(value)
            for row in obj.matrix_world
            for value in row
        )

    meshes = [
        {
            "name": mesh.name,
            "vertices": len(mesh.vertices),
            "polygons": len(mesh.polygons),
            "loops": len(mesh.loops),
            "uv_layers": len(mesh.uv_layers),
            "material_slots": len(mesh.materials),
        }
        for mesh in bpy.data.meshes
    ]
    return {
        "elapsed_seconds": elapsed_seconds,
        "objects": len(bpy.data.objects),
        "objects_by_type": objects_by_type,
        "meshes": meshes,
        "vertices": sum(mesh["vertices"] for mesh in meshes),
        "polygons": sum(mesh["polygons"] for mesh in meshes),
        "materials": len(bpy.data.materials),
        "images": [
            {
                "name": image.name,
                "filepath": image.filepath,
                "colorspace": image.colorspace_settings.name,
            }
            for image in bpy.data.images
            if image.source == "FILE"
        ],
        "actions": action_stats(),
        "frame_range": [
            bpy.context.scene.frame_start,
            bpy.context.scene.frame_end,
        ],
        "fps": (
            bpy.context.scene.render.fps
            / bpy.context.scene.render.fps_base
        ),
        "finite_transforms": finite_transforms,
    }


def validate_scene_stats(stats: dict) -> None:
    if not stats["finite_transforms"]:
        raise ValueError("Imported scene contains non-finite object transforms")
    if stats["objects"] == 0:
        raise ValueError("Importer produced an empty scene")


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    report = {
        "input": str(args.input.resolve()),
        "output": str(args.output.resolve()),
        "blender_version": bpy.app.version_string,
        "status": "failed",
    }
    started = time.perf_counter()

    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        source.register()
        result = bpy.ops.import_scene.blender_max(
            filepath=str(args.input.resolve()),
            use_animation=not args.no_animation,
            use_scene_fps=not args.no_scene_fps,
            animation_fps=args.animation_fps,
            animation_sampling=args.animation_sampling,
            use_image_search=not args.no_image_search,
            texture_directory=(
                str(args.texture_directory.resolve())
                if args.texture_directory
                else ""
            ),
        )
        if "FINISHED" not in result:
            raise RuntimeError("Importer returned %s" % sorted(result))
        report["scene"] = scene_stats(time.perf_counter() - started)
        report["scene"] = scene_stats(time.perf_counter() - started)
        validate_scene_stats(report["scene"])

        args.output.parent.mkdir(parents=True, exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(args.output.resolve()))
        report["status"] = "passed"
    except Exception as exc:
        report["error"] = "%s: %s" % (type(exc).__name__, exc)
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        report["elapsed_seconds"] = time.perf_counter() - started
        write_report(args.report, report)


if __name__ == "__main__":
    main()
