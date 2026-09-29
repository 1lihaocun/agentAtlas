"""Retirement recommendations use retained-log evidence, never quality scores."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from atlas.reviews.retirement import classify_record, partition, build_retirement
from atlas.core.storage import Store


class RetirementTests(unittest.TestCase):
    now = 2_000_000_000

    def record(self, **changes):
        record = {
            "path": "/fixture/AGENTS.md", "version": "v1", "scope": "project",
            "worktree": False, "readable": True, "supported": True,
            "sourceStatus": "available", "lastModified": self.now - 90 * 86400,
            "estimatedSessions": 0, "confirmedReads": None, "mentions": 0,
            "decision": None,
        }
        record.update(changes)
        return record

    def test_classification(self):
        cases = [
            ({}, "review"),
            ({"lastModified": self.now - 89 * 86400}, "recent"),
            ({"lastModified": self.now + 1}, "unknown"),
            ({"lastModified": None}, "unknown"),
            ({"readable": False}, "unknown"),
            ({"sourceStatus": "missing"}, "unknown"),
            ({"sourceStatus": "partial"}, "unknown"),
            ({"supported": False}, "unknown"),
            ({"mentions": 1}, "unknown"),
            ({"estimatedSessions": 1}, "active"),
            ({"confirmedReads": 1}, "active"),
            ({"scope": "user"}, "excluded"),
            ({"worktree": True}, "excluded"),
            ({"decision": {"version": "v1", "action": "keep"}}, "kept"),
            ({"decision": {"version": "v0", "action": "keep"}}, "review"),
            ({"decision": {"version": "v1", "action": "snooze", "until": self.now + 1}}, "snoozed"),
            ({"decision": {"version": "v1", "action": "snooze", "until": self.now}}, "review"),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                self.assertEqual(classify_record(self.record(**changes), self.now)[0], expected)

    def test_partition_is_complete_and_unique(self):
        result = partition([
            self.record(path="/a/AGENTS.md"),
            self.record(path="/b/AGENTS.md", sourceStatus="missing"),
            self.record(path="/c/AGENTS.md", worktree=True),
        ], self.now)
        self.assertEqual(result["total"], 3)
        self.assertEqual(sum(result["counts"].values()), 3)
        self.assertEqual(len(result["rows"]), 3)
        with self.assertRaises(ValueError):
            partition([self.record(), self.record()], self.now)


class RetirementBuildTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[3] / '.agentatlas/work'
        self.tmp = tempfile.TemporaryDirectory(prefix="retirement-test-", dir=str(scratch))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.now = 2_000_000_000
        self.file = self.root / "AGENTS.md"
        self.file.write_bytes(b"## Rules\nPreserve tests.\n")
        os.utime(self.file, (self.now - 100 * 86400,) * 2)
        self.store = Store(self.root / 'state')
        self.index = {"files": [{"path": str(self.file), "scope": "project", "worktree": False,
                                  "kind": "agents", "mtime": 0, "bytes": 0}]}
        self.usage = {"schemaVersion": 2, "tool": "Codex", "days": 30,
                      "sourceStatus": "available", "warnings": [],
                      "files": [{"path": str(self.file), "supported": True,
                                 "estimatedSessions": 0, "confirmedReads": None,
                                 "mentions": 0, "sourceStatus": "available"}]}

    def build(self, **options):
        return build_retirement(self.index, self.usage, self.store, now=self.now, **options)

    def test_uses_live_version_and_mtime_not_index_snapshot(self):
        first = self.build()
        self.assertEqual(first["rows"][0]["bucket"], "review")
        self.assertEqual(first["rows"][0]["version"], hashlib.sha256(self.file.read_bytes()).hexdigest())
        os.utime(self.file, (self.now - 86400,) * 2)
        self.assertEqual(self.build()["rows"][0]["bucket"], "recent")
        self.assertEqual(self.file.read_bytes(), b"## Rules\nPreserve tests.\n")

    def test_current_version_decision_is_used(self):
        version = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.store.decide(str(self.file), version, 'keep', self.now)
        self.assertEqual(self.build()["rows"][0]["bucket"], "kept")
        self.file.write_bytes(b"changed")
        os.utime(self.file, (self.now - 100 * 86400,) * 2)
        self.assertEqual(self.build()["rows"][0]["bucket"], "review")

    def test_known_committed_edit_is_not_lost_when_mtime_is_restored(self):
        version = hashlib.sha256(self.file.read_bytes()).hexdigest()
        self.store.save(str(self.file), version, b'Updated instruction\n', {str(self.file)}, 'test', self.now - 100)
        os.utime(self.file, (self.now - 100 * 86400,) * 2)
        self.assertEqual(self.build()["rows"][0]["bucket"], "recent")

    def test_missing_and_unsupported_files_are_not_silently_dropped(self):
        other = self.root / "CLAUDE.md"
        other.write_bytes(b"Instructions")
        self.index["files"].append({"path": str(other), "scope": "project", "kind": "claude"})
        self.file.unlink()
        result = self.build()
        self.assertEqual(result["total"], 2)
        self.assertEqual(result["counts"]["unknown"], 2)
        gone = next(r for r in result["rows"] if r["path"] == str(self.file))
        self.assertFalse(gone["canDecide"])
        self.assertIsNone(gone["version"])

    def test_absent_usage_row_does_not_mean_zero(self):
        self.usage["files"] = []
        row = self.build()["rows"][0]
        self.assertEqual(row["bucket"], "unknown")
        self.assertIsNone(row["estimatedSessions"])

    def test_legacy_usage_never_drives_recommendations(self):
        self.usage = {"days": 30, "files": [{"path": str(self.file), "sessions": 0, "reads": 0}]}
        self.assertEqual(self.build()["rows"][0]["bucket"], "unknown")

    def test_wrong_window_or_tool_is_unknown(self):
        for changes in ({"days": 90}, {"tool": "Claude Code"}):
            with self.subTest(changes=changes):
                old = dict(self.usage)
                self.usage.update(changes)
                self.assertEqual(self.build()["rows"][0]["bucket"], "unknown")
                self.usage = old

    def test_retargeted_symlink_never_reads_target(self):
        target = self.root / "private.md"
        target.write_bytes(b"private content")
        self.file.unlink()
        self.file.symlink_to(target)
        row = self.build()["rows"][0]
        self.assertEqual(row["bucket"], "unknown")
        self.assertFalse(row["canDecide"])
        self.assertIsNone(row["version"])

    def test_large_preview_does_not_get_whole_file_version(self):
        self.file.write_bytes(b"a" * 32)
        row = self.build(max_file_bytes=8)["rows"][0]
        self.assertEqual(row["bucket"], "unknown")
        self.assertFalse(row["canDecide"])
        self.assertIsNone(row["version"])

    def test_invalid_thresholds_are_rejected(self):
        for kwargs in ({"days": 0}, {"days": 366}, {"days": True}, {"stale_days": "90"}, {"stale_days": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.build(**kwargs)


if __name__ == "__main__":
    unittest.main()
