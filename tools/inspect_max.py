# SPDX-License-Identifier: GPL-2.0-or-later

"""Inspect scene nodes, geometry, modifiers, and controllers in a native MAX file.

Run with Blender:
    blender --background --factory-startup --python tools/inspect_max.py -- input.max
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from source import import_max  # noqa: E402


BYTE_PREFIX_LENGTH = 128
DERIVED_OBJECT_TYPES = {0x2032, 0x2033}
DERIVED_OBJECT_GUIDS = {0x405F22F529263A68}


def parse_args() -> argparse.Namespace:
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-depth", type=int, default=4)
    return parser.parse_args(arguments)


def data_summary(data: Any) -> dict[str, Any]:
    if data is None:
        return {"kind": "none"}
    if isinstance(data, bytes):
        return {
            "kind": "bytes",
            "length": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "hex": data[:BYTE_PREFIX_LENGTH].hex(" "),
        }
    if isinstance(data, str):
        return {"kind": "string", "value": data}
    if isinstance(data, dict):
        return {"kind": "dict", "length": len(data)}
    if isinstance(data, (list, tuple)):
        return {
            "kind": type(data).__name__,
            "length": len(data),
            "values": list(data[:16]),
        }
    return {"kind": type(data).__name__, "value": repr(data)}


def chunk_summary(chunk: Any, depth: int, seen: set[int]) -> dict[str, Any] | None:
    if chunk is None:
        return None

    identity = id(chunk)
    result = {
        "type": f"0x{chunk.types:04X}",
        "guid": f"0x{import_max.get_guid(chunk):016X}",
        "super_id": f"0x{import_max.get_super_id(chunk):08X}",
        "class": import_max.get_cls_name(chunk),
        "data": data_summary(chunk.data),
    }
    if identity in seen:
        result["cycle"] = True
        return result
    if depth <= 0:
        return result

    seen.add(identity)
    children = getattr(chunk, "children", ())
    if children:
        result["children"] = [
            chunk_summary(child, depth - 1, seen.copy())
            for child in children
        ]

    references = import_max.get_references(chunk)
    if references:
        result["references"] = [
            chunk_summary(reference, depth - 1, seen.copy())
            for reference in references
        ]
    return result


def derived_stack_summaries(
    mesh: Any,
    max_depth: int,
) -> list[dict[str, Any]]:
    stacks = []
    seen = set()
    wrapper = mesh
    while (
        wrapper is not None
        and (
            wrapper.types in DERIVED_OBJECT_TYPES
            or import_max.get_guid(wrapper) in DERIVED_OBJECT_GUIDS
        )
        and id(wrapper) not in seen
    ):
        seen.add(id(wrapper))
        references = import_max.get_references(wrapper)
        if not references:
            break

        modifiers = references[:-1]
        mod_apps = [
            child
            for child in getattr(wrapper, "children", ())
            if child.types == 0x2500
        ]
        slots = []
        for index in range(max(len(modifiers), len(mod_apps))):
            modifier = modifiers[index] if index < len(modifiers) else None
            mod_app = mod_apps[index] if index < len(mod_apps) else None
            slots.append(
                {
                    "index": index,
                    "modifier": chunk_summary(
                        modifier,
                        max_depth,
                        set(),
                    ),
                    "mod_app": chunk_summary(
                        mod_app,
                        max_depth,
                        set(),
                    ),
                }
            )

        base_object = references[-1]
        stacks.append(
            {
                "wrapper": chunk_summary(wrapper, 0, set()),
                "slots": slots,
                "base_object": chunk_summary(base_object, 0, set()),
            }
        )
        wrapper = base_object
    return stacks


def inspect_scene(path: Path, max_depth: int) -> dict[str, Any]:
    with import_max.ImportMaxFile(path) as maxfile:
        import_max.read_class_data(maxfile, str(path))
        import_max.read_config(maxfile, str(path))
        import_max.read_directory(maxfile, str(path))
        import_max.read_class_directory(maxfile, str(path))
        import_max.read_video_postqueue(maxfile, str(path))
        import_max.SCENE_LIST = import_max.read_chunks(
            maxfile,
            "Scene",
            import_max.SceneChunk,
        )

        nodes = []
        for index, node in enumerate(import_max.SCENE_LIST[0].children):
            if import_max.get_super_id(node) != 0x1:
                continue
            prs, mesh, material, layer = import_max.get_matrix_mesh_material(node)
            nodes.append(
                {
                    "index": index,
                    "name": import_max.get_node_name(node),
                    "guid": f"0x{import_max.get_guid(node):016X}",
                    "parent": import_max.get_node_name(import_max.get_node_parent(node)),
                    "transform": chunk_summary(prs, max_depth, set()),
                    "mesh_guid": (
                        f"0x{import_max.get_guid(mesh):016X}" if mesh else None
                    ),
                    "mesh": chunk_summary(mesh, max_depth, set()),
                    "derived_stacks": derived_stack_summaries(
                        mesh,
                        max_depth,
                    ),
                    "material_guid": (
                        f"0x{import_max.get_guid(material):016X}"
                        if material
                        else None
                    ),
                    "layer_guid": (
                        f"0x{import_max.get_guid(layer):016X}" if layer else None
                    ),
                }
            )

        return {
            "input": str(path.resolve()),
            "dll_version": maxfile.dll_version,
            "scene_chunks": len(import_max.SCENE_LIST[0].children),
            "nodes": nodes,
        }


def main() -> None:
    args = parse_args()
    report = inspect_scene(args.input, args.max_depth)
    payload = json.dumps(report, indent=2, ensure_ascii=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
