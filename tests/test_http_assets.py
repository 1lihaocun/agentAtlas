import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from atlas import serve
from atlas.asset_files import FileStore
from atlas.asset_settings import SettingsStore


class TestService:
    def __init__(self, root, entries):
        self.file_store = FileStore(lambda: entries, root / "backups")
        self.settings = SettingsStore(root / "state")
        self.entries = entries
        self.refreshes = 0

    def snapshot(self):
        return {"files": self.entries, "totalFiles": len(self.entries)}

    def status(self):
        return {"job": {"running": False}, "index": {"files": len(self.entries)}}

    def refresh(self):
        self.refreshes += 1


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home() / ".hermes/cache/scratch")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.file = self.root / "MEMORY.md"
        self.file.write_text("# One\nfirst\n# Two\nsecond\n", encoding="utf-8")
        self.entries = [{"path": str(self.file), "editable": True, "category": "memory", "searchable": True}]
        self.service = TestService(self.root, self.entries)
        self.server = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        self.server.assets = self.service
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = "http://127.0.0.1:%s" % self.server.server_port

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def call(self, path, body=None, headers=None):
        payload = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json"}
        h.update(headers or {})
        try:
            with urlopen(Request(self.url + path, data=payload, headers=h), timeout=4) as reply:
                return reply.status, json.load(reply)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_asset_routes_and_public_settings(self):
        status, result = self.call("/api/assets")
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["totalFiles"], 1)
        status, result = self.call("/api/settings", {"baseUrl": "https://fixture.example/v1", "apiKey": "test-credential", "model": "fixture"})
        self.assertEqual(status, 200)
        self.assertNotIn("test-credential", json.dumps(result))
        self.assertTrue(self.call("/api/settings")[1]["data"]["apiKeyConfigured"])

    def test_read_write_memory_and_conflict_protection(self):
        from urllib.parse import quote
        status, result = self.call("/api/file?path=" + quote(str(self.file)))
        self.assertEqual(status, 200)
        sha = result["data"]["sha256"]
        status, result = self.call("/api/save", {"path": str(self.file), "content": "updated", "baseSha256": sha})
        self.assertEqual(status, 200)
        self.assertEqual(self.file.read_text(), "updated")
        self.assertEqual(self.service.refreshes, 1)
        status, result = self.call("/api/save", {"path": str(self.file), "content": "stale", "baseSha256": sha})
        self.assertEqual(status, 409)

    def test_unindexed_reads_and_cross_origin_writes_rejected(self):
        from urllib.parse import quote
        unknown = self.root / "unknown.md"
        unknown.write_text("hidden")
        self.assertEqual(self.call("/api/file?path=" + quote(str(unknown)))[0], 403)
        self.assertEqual(self.call("/api/settings", {}, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("/api/assets", headers={"Host": "evil.example"})[0], 403)

    def test_session_readonly_applies_to_all_write_routes(self):
        self.entries[0].update(category="session", editable=False)
        sha = hashlib.sha256(self.file.read_bytes()).hexdigest()
        for path, body in [
            ("/api/save", {"path": str(self.file), "content": "bad", "baseSha256": sha}),
            ("/api/section/save", {"path": str(self.file), "text": "bad", "startLine": 1, "endLine": 2, "baseSha256": sha}),
            ("/api/open", {"path": str(self.file)}),
        ]:
            with self.subTest(path=path):
                self.assertEqual(self.call(path, body)[0], 403)
        self.assertEqual(self.file.read_text(), "# One\nfirst\n# Two\nsecond\n")

    def test_section_save_preserves_siblings(self):
        sha = hashlib.sha256(self.file.read_bytes()).hexdigest()
        status, result = self.call("/api/section/save", {"path": str(self.file), "text": "# One\nchanged", "startLine": 1, "endLine": 2, "baseSha256": sha})
        self.assertEqual(status, 200, result)
        self.assertEqual(self.file.read_text(), "# One\nchanged\n# Two\nsecond\n")


if __name__ == "__main__":
    unittest.main()
