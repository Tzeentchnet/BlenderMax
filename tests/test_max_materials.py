# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from source.max_materials import TextureResolver, clear_texture_cache  # noqa: E402


class TextureResolverTests(unittest.TestCase):
    def setUp(self):
        clear_texture_cache()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.source_dir = root / "depot"
        self.source_dir.mkdir()
        self.max_file = self.source_dir / "scene.max"
        self.max_file.write_bytes(b"")
        self.beside = self.source_dir / "Wall_Diffuse.TGA"
        self.beside.write_bytes(b"")
        self.texture_dir = root / "override"
        self.texture_dir.mkdir()
        self.override = self.texture_dir / "wall_diffuse.tga"
        self.override.write_bytes(b"")

    def tearDown(self):
        self.tmp.cleanup()
        clear_texture_cache()

    def test_import_folder_checked_first_when_preferred(self):
        self.override.unlink()
        resolver = TextureResolver(
            str(self.max_file), str(self.texture_dir), prefer_source_directory=True
        )
        found = resolver.resolve(r"C:\artist\maps\wall_diffuse.tga")
        self.assertEqual(found.resolve(), self.beside.resolve())

    def test_texture_directory_wins_over_import_folder(self):
        resolver = TextureResolver(
            str(self.max_file), str(self.texture_dir), prefer_source_directory=True
        )
        found = resolver.resolve(r"C:\artist\maps\wall_diffuse.tga")
        self.assertEqual(found.resolve(), self.override.resolve())

    def test_texture_directory_subfolders_win_over_import_folder(self):
        self.override.unlink()
        nested = self.texture_dir / "walls" / "wall_diffuse.tga"
        nested.parent.mkdir()
        nested.write_bytes(b"")
        resolver = TextureResolver(
            str(self.max_file), str(self.texture_dir), prefer_source_directory=True
        )
        self.assertEqual(resolver.resolve("wall_diffuse.tga").resolve(), nested.resolve())

    def test_texture_directory_wins_without_preference(self):
        resolver = TextureResolver(str(self.max_file), str(self.texture_dir))
        found = resolver.resolve("wall_diffuse.tga")
        self.assertEqual(found.resolve(), self.override.resolve())

    def test_falls_back_when_missing_from_import_folder(self):
        other = self.texture_dir / "floor.tga"
        other.write_bytes(b"")
        resolver = TextureResolver(
            str(self.max_file), str(self.texture_dir), prefer_source_directory=True
        )
        self.assertEqual(resolver.resolve("floor.tga").resolve(), other.resolve())


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
