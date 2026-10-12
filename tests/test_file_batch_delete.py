"""Bulk deletion keeps mixed folders recoverable and rejects unsafe selections."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.core.file_trash import FileTrashError, move_many, restore_many


class FileBatchDeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.roots = {name: root / name for name in ("inbox", "outputs", "templates")}
        for folder in self.roots.values():
            folder.mkdir()
        self.trash = root / "trash"

    def tearDown(self):
        self.temp.cleanup()

    def test_mixed_batch_can_be_restored(self):
        (self.roots["inbox"] / "源数据.xlsx").write_bytes(b"source")
        (self.roots["outputs"] / "结果.xlsx").write_bytes(b"result")
        items = [{"where": "inbox", "name": "源数据.xlsx"},
                 {"where": "outputs", "name": "结果.xlsx"}]
        moved = move_many(items, self.roots, self.trash)
        self.assertFalse((self.roots["inbox"] / "源数据.xlsx").exists())
        self.assertFalse((self.roots["outputs"] / "结果.xlsx").exists())
        self.assertEqual(restore_many(moved["token"], self.roots, self.trash), items)
        self.assertEqual((self.roots["inbox"] / "源数据.xlsx").read_bytes(), b"source")
        self.assertEqual((self.roots["outputs"] / "结果.xlsx").read_bytes(), b"result")

    def test_invalid_selection_does_not_move_valid_file(self):
        (self.roots["inbox"] / "保留.csv").write_bytes(b"keep")
        for bad in ({"where": "inbox", "name": "../outside.csv"},
                    {"where": "inbox", "name": "missing.csv"},
                    {"where": "unknown", "name": "x.csv"}):
            with self.assertRaises(FileTrashError):
                move_many([{"where": "inbox", "name": "保留.csv"}, bad], self.roots, self.trash)
            self.assertTrue((self.roots["inbox"] / "保留.csv").exists())

    def test_restore_refuses_to_overwrite_new_file(self):
        file = self.roots["outputs"] / "模板.xlsx"
        file.write_bytes(b"old")
        moved = move_many([{"where": "outputs", "name": file.name}], self.roots, self.trash)
        file.write_bytes(b"new")
        with self.assertRaises(FileTrashError):
            restore_many(moved["token"], self.roots, self.trash)
        self.assertEqual(file.read_bytes(), b"new")

    def test_templates_are_not_in_deletable_roots(self):
        file = self.roots["templates"] / "平台模板.xlsx"
        file.write_bytes(b"protected")
        with self.assertRaises(FileTrashError):
            move_many([{"where": "templates", "name": file.name}],
                      {"inbox": self.roots["inbox"], "outputs": self.roots["outputs"]}, self.trash)
        self.assertTrue(file.exists())


if __name__ == "__main__":
    unittest.main()
