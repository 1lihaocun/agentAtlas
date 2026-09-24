"""Isolated real HTTP tests for lifecycle integration; never touches user HOME."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import quote
from datetime import datetime, timezone

from atlas import serve
from atlas.asset_files import FileStore
from atlas.lifecycle import ReviewService
from atlas.storage import Store


class Assets:
    def __init__(self, entries, state):
        self.file_store = FileStore(lambda: entries, state / "backups")
        self.refreshes = 0

    def refresh(self):
        self.refreshes += 1


class HttpLifecycleTests(unittest.TestCase):
    def test_evidence_and_legacy_alias_use_same_metadata_only_contract(self):
        stamp = datetime.fromtimestamp(self.now - 30, timezone.utc).isoformat()
        event = {"type":"response_item", "timestamp":stamp,
                 "payload":{"text":"PRIVATE FIXTURE TEXT: " + str(self.file)}}
        with (self.logs / "sample.jsonl").open("a") as stream:
            stream.write(json.dumps(event) + "\n")
        for endpoint in ("/api/usage/evidence", "/api/usage/sessions"):
            with self.subTest(endpoint=endpoint):
                status, reply = self.call(endpoint + "?path=" + quote(str(self.file)))
                self.assertEqual(status, 200, reply)
                data = reply["data"]
                self.assertEqual(data["mentions"], 1)
                self.assertIsNone(data["confirmedReads"])
                self.assertEqual(data["evidence"][0]["kind"], "mention")
                self.assertEqual(data["detailsReturned"], len(data["evidence"]))
                self.assertIn("current_layout_not_historical", data["assumptions"])
                self.assertNotIn("PRIVATE FIXTURE TEXT", json.dumps(data))
        self.assertEqual(self.call("/api/usage/evidence?path=" + quote(str(self.root / "private.md")))[0], 403)

    def test_default_effective_is_tool_specific_metadata_not_mixed_contents(self):
        (self.repo / "CLAUDE.md").write_text("PRIVATE OTHER TOOL CONTENT")
        (self.repo / "AGENTS.override.md").write_text("override")
        status, reply = self.call("/api/effective?dir=" + quote(str(self.repo)))
        self.assertEqual(status, 200, reply)
        data = reply["data"]
        self.assertEqual(data["tool"], "Codex")
        self.assertEqual([row["path"] for row in data["files"]], [str(self.repo / "AGENTS.override.md")])
        self.assertFalse(data["files"][0]["indexed"])
        self.assertNotIn("content", data["files"][0])
        self.assertNotIn("PRIVATE OTHER TOOL CONTENT", json.dumps(data))
        for suffix in ("&tool=Claude", "&tool=Codex&tool=Codex", "&dir=" + quote(str(self.repo))):
            self.assertEqual(self.call("/api/effective?dir=" + quote(str(self.repo)) + suffix)[0], 400)

    def test_file_save_journals_version_and_backup(self):
        loaded = self.read_file()
        original = self.file.read_bytes()
        status, saved = self.call("/api/save", {
            "path": str(self.file), "content": "## New\n", "baseVersion": loaded["version"]})
        self.assertEqual(status, 200)
        edits = self.history.edits(str(self.file))
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["status"], "committed")
        self.assertEqual(edits[0]["before_version"], loaded["version"])
        self.assertEqual(Path(edits[0]["backup_path"]).read_bytes(), original)
        self.assertEqual(saved["data"]["version"], hashlib.sha256(self.file.read_bytes()).hexdigest())

    def setUp(self):
        scratch = Path(os.environ.get("TMPDIR", str(Path.home() / ".hermes/cache/scratch")))
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-http-lifecycle-", dir=str(scratch))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.now = 2_000_000_000
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.file = self.repo / "AGENTS.md"
        self.file.write_bytes(b"## One\r\nold\r\n## Two\r\nkeep\r\n")
        os.utime(self.file, (self.now - 100 * 86400,) * 2)
        self.entries = [{"path": str(self.file), "editable": True, "searchable": True,
                         "category": "instruction", "scope": "project", "kind": "agents", "worktree": False}]
        self.index = {"files": self.entries}
        self.logs = self.root / "codex" / "sessions"
        self.logs.mkdir(parents=True)
        other = self.root / "other"
        other.mkdir()
        stamp = datetime.fromtimestamp(self.now - 3600, timezone.utc).isoformat()
        (self.logs / "sample.jsonl").write_text(json.dumps({"type": "session_meta", "timestamp": stamp,
            "payload": {"id": "fixture-session", "cwd": str(other)}}) + "\n")
        self.assets = Assets(self.entries, self.root / "state")
        self.history = Store(self.root / "state" / "lifecycle")
        self.server = serve.ThreadingHTTPServer(("127.0.0.1", 0), serve.Handler)
        self.server.assets = self.assets
        self.server.lifecycle = ReviewService(lambda: self.index, self.assets.file_store,
            self.history, self.logs, self.logs.parent, clock=lambda: self.now)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.url = "http://127.0.0.1:%s" % self.server.server_port

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def call(self, path, body=None, headers=None):
        payload = None if body is None else json.dumps(body).encode()
        headers = dict({"Content-Type": "application/json"}, **(headers or {}))
        try:
            with urlopen(Request(self.url + path, data=payload, headers=headers), timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def read_file(self):
        status, reply = self.call("/api/file?path=" + quote(str(self.file)))
        self.assertEqual(status, 200, reply)
        return reply["data"]

    def test_queue_decisions_persist_without_editing_source(self):
        original = self.file.read_bytes()
        status, reply = self.call("/api/retirement")
        self.assertEqual(status, 200, reply)
        data = reply["data"]
        self.assertEqual(data["counts"]["review"], 1)
        row = data["rows"][0]
        decision = {"path": str(self.file), "baseVersion": row["version"], "action": "keep"}
        self.assertEqual(self.call("/api/retirement/decision", decision)[0], 200)
        self.assertEqual(self.call("/api/retirement")[1]["data"]["counts"]["kept"], 1)
        reopened = Store(self.root / "state" / "lifecycle")
        self.assertEqual(reopened.review(str(self.file), row["version"])["action"], "keep")
        self.assertEqual(reopened.edits(), [])
        self.assertEqual(self.file.read_bytes(), original)
        decision.update(action="reset")
        self.assertEqual(self.call("/api/retirement/decision", decision)[0], 200)
        decision.update(action="snooze", snoozeDays=30)
        self.assertEqual(self.call("/api/retirement/decision", decision)[0], 200)
        self.assertEqual(self.call("/api/retirement")[1]["data"]["counts"]["snoozed"], 1)

    def test_stale_decision_and_invalid_thresholds_rejected(self):
        version = self.read_file()["version"]
        self.file.write_bytes(b"external")
        status, _ = self.call("/api/retirement/decision", {"path": str(self.file), "baseVersion": version, "action": "keep"})
        self.assertEqual(status, 409)
        for query in ("days=0", "days=", "days=30&days=90", "staleDays=x", "tool=Claude"):
            self.assertEqual(self.call("/api/retirement?" + query)[0], 400, query)
        self.assertEqual(self.file.read_bytes(), b"external")

    def test_versioned_sections_preserve_bytes_and_reject_stale_ids(self):
        loaded = self.read_file()
        status, reply = self.call("/api/sections?path=" + quote(str(self.file)))
        self.assertEqual(status, 200, reply)
        data = reply["data"]
        self.assertEqual(data["version"], loaded["version"])
        body = {"path": str(self.file), "baseVersion": loaded["version"],
                "sectionId": data["sections"][0]["id"], "text": "## One\nnew\n"}
        status, reply = self.call("/api/section/save", body)
        self.assertEqual(status, 200, reply)
        self.assertEqual(self.file.read_bytes(), b"## One\r\nnew\r\n## Two\r\nkeep\r\n")
        self.assertEqual(self.call("/api/section/save", body)[0], 409)
        self.assertEqual(self.call("/api/save", {"path": str(self.file), "content": "bad"})[0], 428)

    def test_usage_is_v2_and_untrusted_origins_rejected(self):
        status, reply = self.call("/api/usage")
        self.assertEqual(status, 200, reply)
        self.assertEqual(reply["data"]["schemaVersion"], 2)
        self.assertIsNone(reply["data"]["files"][0]["confirmedReads"])
        self.assertEqual(self.call("/api/retirement/decision", {}, {"Origin": "null"})[0], 403)
        self.assertEqual(self.call("/api/retirement/decision", {}, {"Origin": "https://evil.example"})[0], 403)

    def test_static_renderer_is_served_and_unindexed_decision_denied(self):
        with urlopen(self.url + "/lifecycle.js", timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertIn(b"AtlasLifecycle", response.read())
        other = self.root / "other" / "AGENTS.md"
        other.write_bytes(b"hidden")
        status, _ = self.call("/api/retirement/decision", {
            "path": str(other), "baseVersion": hashlib.sha256(b"hidden").hexdigest(), "action": "keep"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
