# SPDX-License-Identifier: GPL-2.0-or-later

"""Run repeatable BlenderMax imports against a MAX-file corpus."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLENDER = Path(r"D:\Blender Foundation\Blender 5.2\blender.exe")
DEFAULT_CORPUS = Path(r"F:\ModelArchive\hl2\hl2")
DEFAULT_OUTPUT = Path(r"E:\tests\BlenderMax\corpus")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender", type=Path, default=DEFAULT_BLENDER)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Text file containing corpus-relative MAX paths",
    )
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--animation-fps", type=float, default=30.0)
    parser.add_argument("--no-scene-fps", action="store_true")
    parser.add_argument(
        "--animation-sampling",
        choices=("BAKE", "KEYS"),
        default="BAKE",
    )
    return parser.parse_args()


def corpus_files(args: argparse.Namespace) -> list[Path]:
    if args.manifest:
        lines = args.manifest.read_text(encoding="utf-8").splitlines()
        return [
            args.corpus / line.strip()
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        ]

    files = sorted(args.corpus.rglob("*.max"), key=lambda path: (path.stat().st_size, str(path).casefold()))
    if args.all or len(files) <= args.limit:
        return files

    keyword_files = [
        path
        for path in files
        if any(
            keyword in path.name.casefold()
            for keyword in ("idle", "walk", "run", "anim", "reference")
        )
    ]
    selected = []
    for pool in (files, keyword_files):
        if not pool:
            continue
        slots = max(1, args.limit // 2)
        for index in range(slots):
            offset = round(index * (len(pool) - 1) / max(1, slots - 1))
            candidate = pool[offset]
            if candidate not in selected:
                selected.append(candidate)
    return selected[:args.limit]


def output_paths(args: argparse.Namespace, source: Path) -> tuple[Path, Path, Path]:
    relative = source.relative_to(args.corpus)
    blend = (args.output / "blend" / relative).with_suffix(".blend")
    report = (args.output / "reports" / relative).with_suffix(".json")
    log = (args.output / "logs" / relative).with_suffix(".log")
    return blend, report, log


def run_import(args: argparse.Namespace, source: Path) -> dict:
    blend, report, log = output_paths(args, source)
    if args.skip_existing and blend.is_file() and report.is_file():
        result = json.loads(report.read_text(encoding="utf-8"))
        result["skipped"] = True
        return result

    report.unlink(missing_ok=True)
    texture_directory = args.corpus / "materialsrc"
    command = [
        str(args.blender),
        "--background",
        "--factory-startup",
        "--python",
        str(REPOSITORY_ROOT / "tools" / "blender_import.py"),
        "--",
        str(source),
        str(blend),
        str(report),
        "--animation-fps",
        str(args.animation_fps),
        "--animation-sampling",
        args.animation_sampling,
    ]
    if texture_directory.is_dir():
        command.extend(("--texture-directory", str(texture_directory)))
    if args.no_scene_fps:
        command.append("--no-scene-fps")

    started = time.perf_counter()
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
            check=False,
        )
        output = process.stdout + process.stderr
        status = "pending"
        error = (
            None
            if process.returncode == 0
            else "Blender exited with code %d" % process.returncode
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        status = "timeout"
        error = "Import exceeded %d seconds" % args.timeout

    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(output, encoding="utf-8")

    if report.is_file():
        result = json.loads(report.read_text(encoding="utf-8"))
    else:
        result = {
            "input": str(source),
            "output": str(blend),
        }
        error = error or "Blender did not write an import report"

    if status != "timeout":
        status = (
            "passed"
            if process.returncode == 0 and result.get("status") == "passed"
            else "failed"
        )
        if status == "failed":
            error = error or result.get("error") or "Import report did not pass"
    result.update(
        {
            "status": status,
            "error": error or result.get("error"),
            "log": str(log),
            "elapsed_seconds": time.perf_counter() - started,
        }
    )
    return result


def main() -> int:
    args = parse_args()
    for required, label in (
        (args.blender, "Blender executable"),
        (args.corpus, "corpus directory"),
    ):
        if not required.exists():
            raise FileNotFoundError("%s does not exist: %s" % (label, required))

    files = corpus_files(args)
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for index, source in enumerate(files, start=1):
        print("[%d/%d] %s" % (index, len(files), source.relative_to(args.corpus)), flush=True)
        result = run_import(args, source)
        results.append(result)
        print("  %s (%.2fs)" % (result["status"], result["elapsed_seconds"]), flush=True)

    summary = {
        "blender": str(args.blender),
        "corpus": str(args.corpus),
        "output": str(args.output),
        "files": len(results),
        "passed": sum(result["status"] == "passed" for result in results),
        "failed": sum(result["status"] == "failed" for result in results),
        "timeouts": sum(result["status"] == "timeout" for result in results),
        "results": results,
    }
    summary_path = args.output / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    print("Summary: %s" % summary_path)
    return 0 if summary["failed"] == 0 and summary["timeouts"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
