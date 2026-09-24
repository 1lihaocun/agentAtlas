"""HTTP contract tests; semantic rule fixtures live in test_memory_scope.py."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from atlas import serve
from atlas.asset_files import FileStore


class MemoryService:
    def __init__(self, entry):
        self.entry = entry
        self.calls = []
        self.file_store = FileStore(lambda: [self.entry], Path(entry["path"]).parent / "backups")

    def memories(self, project="", platform="", profile=""):
        self.calls.append((project, platform, profile))
        return {"files": [dict(self.entry, semantics={
            "level": "unknown", "label": "作用域未知",
            "observation": {"state": "unverified", "label": "未验证实际读取"}},
            memoryInfo={"title": "Fixture title", "relatedProjects": [], "status": "ready"},
            memoryStorage={"directory": str(Path(self.entry['path']).parent)})],
            "selected": {"project": project, "platform": platform, "profile": profile},
            "counts": {"total": 1, "matched": 1, "levels": {"unknown": 1}}}


class MemoryHttpTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.home() / ".hermes/cache/scratch")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.file = self.root / "MEMORY.md"
        self.file.write_text("synthetic memory fixture")
        self.service = MemoryService({"path": str(self.file), "category": "memory",
                                      "platform": "fixture", "editable": True, "searchable": True})
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

    def request(self, suffix, headers=None):
        try:
            with urlopen(Request(self.url + suffix, headers=headers or {}), timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def test_memory_view_delegates_applicability_filters_without_rewriting(self):
        project = str(self.root / "项目-with-hyphens")
        status, payload = self.request("/api/memories?project=" + quote(project) + "&platform=hermes&profile=work")
        self.assertEqual(status, 200, payload)
        self.assertEqual(self.service.calls, [(project, "hermes", "work")])
        self.assertEqual(payload["data"]["counts"]["matched"], 1)

    def test_memory_view_rejects_unknown_or_duplicate_filters(self):
        for query in ("platform=hermes&platform=codex", "project=a&project=b", "profile=a&profile=b", "path=/private", "project=%00"):
            with self.subTest(query=query):
                status, payload = self.request("/api/memories?" + query)
                self.assertEqual(status, 400, payload)
        self.assertEqual(self.service.calls, [])

    def test_file_preview_adds_semantics_without_claiming_actual_read(self):
        status, payload = self.request("/api/file?path=" + quote(str(self.file)))
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["content"], "synthetic memory fixture")
        self.assertEqual(payload["data"]["semantics"]["observation"]["state"], "unverified")
        self.assertEqual(len(self.service.calls), 1)

    def test_other_file_previews_do_not_invoke_memory_resolver(self):
        self.service.entry["category"] = "instruction"
        status, payload = self.request("/api/file?path=" + quote(str(self.file)))
        self.assertEqual(status, 200, payload)
        self.assertNotIn("semantics", payload["data"])
        self.assertEqual(self.service.calls, [])

    def test_file_preview_contains_same_descriptive_metadata_as_scope_view(self):
        status, payload = self.request("/api/file?path=" + quote(str(self.file)))
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload['data'].get('memoryInfo'), {
            'title': 'Fixture title', 'relatedProjects': [], 'status': 'ready'})

    def test_file_preview_supplies_authoritative_storage_location(self):
        status, payload = self.request('/api/file?path=' + quote(str(self.file)))
        self.assertEqual(status, 200)
        self.assertEqual(payload['data'].get('memoryStorage'), {'directory': str(self.root)})

    def test_unindexed_and_cross_origin_cannot_access_memory_details(self):
        self.assertEqual(self.request("/api/file?path=" + quote(str(self.root / "private.md")))[0], 403)
        self.assertEqual(self.request("/api/memories", {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.service.calls, [])


if __name__ == "__main__":
    unittest.main()
