# SPDX-FileCopyrightText: 2026 BlenderMax contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

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
        prefer_source_directory: bool = False,
    ):
        self.source_file = Path(source_file).resolve()
        self.recursive = recursive
        self.prefer_source_directory = prefer_source_directory
        override = Path(texture_directory).resolve() if texture_directory else None
        self.override_root = override if override is not None and override.is_dir() else None
        self.roots = self._build_roots(texture_directory)
        self._source_listing: dict[str, Path] | None = None
        self._relocated: dict[str, Path | None] | None = None

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
        if self.override_root is not None:
            # An explicitly set texture directory always wins.
            match = self._find_in_root(self.override_root, texture_path)
            if match is not None:
                return match

        if self.prefer_source_directory:
            match = self._find_beside_source(texture_path.name)
            if match is not None:
                return match

        if texture_path.is_absolute() and texture_path.is_file():
            return texture_path

        match = self._find_relocated(texture)
        if match is not None:
            return match

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

    def _find_relocated(self, texture: str) -> Path | None:
        """Re-root a stored path whose project tree moved.

        A scene saved as ``X:/Depot/Game/Level/Textures/a.bmp`` and opened from
        ``F:/Copy/Level/Scenes/b.max`` keeps the tail ``Level/Textures/a.bmp``.
        The longest directory tail that exists under an ancestor of the scene
        wins; nearer ancestors are tried first for each tail length.
        """
        stored = PureWindowsPath(texture.replace("/", "\\"))
        directories = [part for part in stored.parts[:-1] if part != stored.anchor]
        if not directories or not stored.name:
            return None
        if self._relocated is None:
            self._relocated = {}
        key = texture.casefold()
        if key in self._relocated:
            return self._relocated[key]
        match = None
        ancestors = (self.source_file.parent, *self.source_file.parent.parents)
        for length in range(len(directories), 0, -1):
            tail = directories[-length:]
            for ancestor in ancestors:
                candidate = ancestor.joinpath(*tail, stored.name)
                if candidate.is_file():
                    match = candidate
                    break
            if match is not None:
                break
        self._relocated[key] = match
        return match

    def _find_in_root(self, root: Path, texture_path: Path) -> Path | None:
        if not texture_path.is_absolute():
            direct = root / texture_path
            if direct.is_file():
                return direct
        by_name = root / texture_path.name
        if by_name.is_file():
            return by_name
        if self.recursive:
            return self._index(root).get(texture_path.name.casefold())
        return None

    def _find_beside_source(self, name: str) -> Path | None:
        """First-pass lookup in the folder the .max file was imported from.

        Matches by basename, case-insensitively, without descending into
        subfolders, so it stays fast even on large source depots.
        """
        if not name:
            return None
        if self._source_listing is None:
            listing = {}
            try:
                with os.scandir(self.source_file.parent) as entries:
                    for entry in entries:
                        if entry.is_file():
                            listing.setdefault(entry.name.casefold(), Path(entry.path))
            except OSError:
                pass
            self._source_listing = listing
        return self._source_listing.get(name.casefold())

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
