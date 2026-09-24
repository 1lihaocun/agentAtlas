import hashlib
from pathlib import Path
import tempfile
import unittest

from atlas.asset_files import FileStore, FileProblem


class FileStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home() / ".hermes/cache/scratch")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.file = self.root / "MEMORY.md"
        self.file.write_text("# Memory\noriginal\n", encoding="utf-8")
        self.entries = [{"path": str(self.file), "editable": True, "category": "memory", "searchable": True}]
        self.store = FileStore(lambda: self.entries, self.root / "backups")

    def test_index_required_for_read_and_write(self):
        unknown = self.root / "not-indexed.md"
        unknown.write_text("private")
        for operation in (lambda: self.store.read(str(unknown)), lambda: self.store.save(str(unknown), "bad", "")):
            with self.assertRaises(FileProblem) as caught:
                operation()
            self.assertEqual(caught.exception.status, 403)
        self.assertEqual(unknown.read_text(), "private")

    def test_readonly_and_changed_file_cannot_be_saved(self):
        read = self.store.read(str(self.file))
        self.file.write_text("external edit")
        with self.assertRaises(FileProblem) as caught:
            self.store.save(str(self.file), "replacement", read["sha256"])
        self.assertEqual(caught.exception.status, 409)
        self.entries[0]["editable"] = False
        with self.assertRaises(FileProblem) as caught:
            self.store.save(str(self.file), "replacement", hashlib.sha256(b"external edit").hexdigest())
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(self.file.read_text(), "external edit")

    def test_save_has_unique_durable_backups_and_refresh_hash(self):
        original = self.file.read_bytes()
        first = self.store.save(str(self.file), "first", self.store.read(str(self.file))["sha256"])
        second = self.store.save(str(self.file), "second", first["sha256"])
        self.assertEqual(Path(first["backup"]).read_bytes(), original)
        self.assertEqual(Path(second["backup"]).read_bytes(), b"first")
        self.assertNotEqual(first["backup"], second["backup"])
        self.assertEqual(self.file.read_bytes(), b"second")
        self.assertFalse(self.store.save(str(self.file), "second", second["sha256"])["changed"])

    def test_preview_limit_never_allows_truncated_write(self):
        read = self.store.read(str(self.file), max_bytes=6)
        self.assertTrue(read["truncated"])
        self.assertFalse(read["editable"])
        self.assertIsNone(read["sha256"])
        with self.assertRaises(FileProblem):
            self.store.save(str(self.file), "partial", None)

    def test_symlink_retarget_rejected(self):
        outside = self.root / "outside.md"
        outside.write_text("outside")
        self.file.unlink()
        self.file.symlink_to(outside)
        with self.assertRaises(FileProblem):
            self.store.read(str(self.file))
        self.assertEqual(outside.read_text(), "outside")

    def test_config_gaining_credentials_after_scan_is_not_previewed(self):
        config = self.root / "settings.json"
        config.write_text('{"theme":"dark"}')
        self.entries.append({"path": str(config), "category": "config", "editable": False, "searchable": True})
        self.assertIn("theme", self.store.read(str(config))["content"])
        config.write_text('{"apiKey":"synthetic-test-value"}')
        with self.assertRaises(FileProblem) as caught:
            self.store.read(str(config))
        self.assertEqual(caught.exception.status, 403)

    def test_damaged_utf8_is_readable_but_never_editable(self):
        self.file.write_bytes(b"valid\n\xff\n")
        data = self.store.read(str(self.file))
        self.assertIn("valid", data["content"])
        self.assertFalse(data["editable"])


if __name__ == "__main__":
    unittest.main()
