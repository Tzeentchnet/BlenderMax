# SPDX-FileCopyrightText: 2026 BlenderMax contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
from pathlib import Path

from bpy_extras.image_utils import load_image


_TEXTURE_DIRECTORIES = ("materialsrc", "materials", "textures", "maps")
_INDEX_CACHE: dict[Path, dict[str, Path]] = {}


def _normalized_path(path: str) -> Path:
    return Path(path.replace("\\", os.sep).replace("/", os.sep))


class TextureResolver:
    def __init__(
        self,
        source_file: str,
        texture_directory: str = "",
        recursive: bool = True,
    ):
        self.source_file = Path(source_file).resolve()
        self.recursive = recursive
        self.roots = self._build_roots(texture_directory)

    def _build_roots(self, texture_directory: str) -> tuple[Path, ...]:
        roots = []
        if texture_directory:
            roots.append(Path(texture_directory).resolve())
        roots.append(self.source_file.parent)

        if self.recursive:
            for parent in self.source_file.parents:
                for name in _TEXTURE_DIRECTORIES:
                    candidate = parent / name
                    if candidate.is_dir():
                        roots.append(candidate.resolve())

        unique = []
        seen = set()
        for root in roots:
            if root.is_dir() and root not in seen:
                seen.add(root)
                unique.append(root)
        return tuple(unique)

    def resolve(self, texture: str | None) -> Path | None:
        if not texture:
            return None

        texture_path = _normalized_path(texture)
        if texture_path.is_absolute() and texture_path.is_file():
            return texture_path

        for root in self.roots:
            direct = root / texture_path
            if direct.is_file():
                return direct
            by_name = root / texture_path.name
            if by_name.is_file():
                return by_name

        if not self.recursive:
            return None

        target = texture_path.name.casefold()
        for root in self.roots:
            index = self._index(root)
            match = index.get(target)
            if match is not None:
                return match
        return None

    def load(self, texture: str | None, *, data: bool = False):
        resolved = self.resolve(texture)
        if resolved is None:
            return None
        image = load_image(
            resolved.name,
            str(resolved.parent),
            place_holder=False,
            recursive=False,
            check_existing=True,
        )
        if image is not None and data:
            image.colorspace_settings.name = "Non-Color"
        return image

    @staticmethod
    def _index(root: Path) -> dict[str, Path]:
        cached = _INDEX_CACHE.get(root)
        if cached is not None:
            return cached

        index = {}
        for directory, _, filenames in os.walk(root):
            base = Path(directory)
            for filename in filenames:
                index.setdefault(filename.casefold(), base / filename)
        _INDEX_CACHE[root] = index
        return index


def clear_texture_cache() -> None:
    _INDEX_CACHE.clear()
