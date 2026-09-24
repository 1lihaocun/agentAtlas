"""Synthetic retained logs only; never scan a real Codex home."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from atlas import usage


class UsageTests(unittest.TestCase):
    def setUp(self):
        scratch = Path.home() / ".hermes/cache/scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix="atlas-usage-", dir=str(scratch))
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "sessions"
        self.home = self.root / "codex"
        self.repo = self.root / "repo"
        for path in (self.source, self.home, self.repo):
            path.mkdir()
        (self.repo / ".git").mkdir()
        self.path = self.repo / "AGENTS.md"
        self.path.write_text("fixture instructions")
        self.now = 2_000_000_000
        self.index = {"files": [{"path": str(self.path), "kind": "agents"}]}

    def event(self, kind, payload, at=None):
        at = self.now - 60 if at is None else at
        return {"type": kind, "timestamp": datetime.fromtimestamp(
            at, timezone.utc).isoformat().replace("+00:00", "Z"), "payload": payload}

    def log(self, name, *events):
        path = self.source / name
        path.write_text("".join(json.dumps(event) + "\n" for event in events))
        return path

    def compute(self, **kwargs):
        return usage.compute_usage(self.index, self.source, self.home,
                                   now=self.now, use_cache=False, **kwargs)

    def test_distinct_session_event_window_and_old_metadata(self):
        old = self.now - 40 * 86400
        meta = self.event("session_meta", {"id": "s1", "cwd": str(self.repo)}, old)
        self.log("one.jsonl", meta,
                 self.event("response_item", {"text": str(self.path)}, old),
                 self.event("turn_context", {"cwd": str(self.repo)}),
                 self.event("event_msg", {"text": "recent activity"}))
        self.log("duplicate.jsonl", meta, self.event("turn_context", {"cwd": str(self.repo)}))
        data = self.compute()
        row = data["files"][0]
        self.assertEqual(data["schemaVersion"], 2)
        self.assertEqual(data["tool"], "Codex")
        self.assertEqual(data["observationScope"], "retained_logs_only")
        self.assertEqual(data["sessionsScanned"], 1)
        self.assertEqual(row["estimatedSessions"], 1)
        self.assertEqual(row["mentions"], 0)
        self.assertIsNone(row["confirmedReads"])
        self.assertEqual(row["lastEffective"], self.now - 60)
        self.assertEqual(data["sourceStatus"], "available")

    def test_mentions_are_not_reads_even_successful_shell(self):
        other = self.root / "other"
        other.mkdir()
        meta = self.event("session_meta", {"id": "s", "cwd": str(other)})
        self.log("mention.jsonl", meta,
                 self.event("response_item", {"type": "message", "role": "user",
                     "content": [{"text": "I read `" + str(self.path) + "`"}]}),
                 self.event("response_item", {"type": "function_call", "name": "shell",
                     "call_id": "c", "arguments": json.dumps({"command": "cat " + str(self.path)})}),
                 self.event("response_item", {"type": "function_call_output", "call_id": "c",
                     "output": "exit code 0: SECRET RAW MESSAGE"}))
        data = self.compute()
        row = data["files"][0]
        self.assertEqual(row["mentions"], 1)
        self.assertEqual(row["estimatedSessions"], 0)
        self.assertIsNone(row["confirmedReads"])
        self.assertEqual(row["readStatus"], "not_instrumented")
        self.assertNotIn("SECRET RAW MESSAGE", json.dumps(data))
        self.assertEqual(row["byTool"]["Codex"]["mentions"], 1)

    def test_mentions_support_quoted_spaces_and_exact_boundaries(self):
        path = self.repo / "a space" / "AGENTS.md"
        path.parent.mkdir()
        path.write_text("rules")
        self.index["files"].append({"path": str(path)})
        meta = self.event("session_meta", {"id": "s"})
        self.log("spaces.jsonl", meta, self.event("response_item", {
            "text": "A `" + str(path) + "` and " + str(self.path) + ".backup"}))
        data = {row["path"]: row for row in self.compute()["files"]}
        self.assertEqual(data[str(path)]["mentions"], 1)
        self.assertFalse(data[str(self.path)]["mentions"])

    def test_missing_cwd_does_not_invent_project_or_global_exposure(self):
        global_path = self.home / "AGENTS.md"
        global_path.write_text("global")
        self.index["files"].append({"path": str(global_path)})
        self.log("no-cwd.jsonl", self.event("session_meta", {"id": "s"}),
                 self.event("event_msg", {"text": str(self.path)}))
        data = self.compute()
        self.assertEqual(data["sourceStatus"], "partial")
        self.assertIn("missing_cwd", data["warnings"])
        self.assertTrue(all(row["estimatedSessions"] is None for row in data["files"]))
        self.assertEqual(data["sessionsScanned"], 1)

    def test_deleted_cwd_preserves_mentions_but_unknown_estimates(self):
        self.log("deleted.jsonl", self.event("session_meta", {
            "id": "s", "cwd": str(self.repo / "deleted")}),
            self.event("event_msg", {"text": str(self.path)}))
        data = self.compute()
        self.assertEqual(data["files"][0]["mentions"], 1)
        self.assertIsNone(data["files"][0]["estimatedSessions"])
        self.assertEqual(data["sourceStatus"], "partial")
        self.assertIn("cwd_unavailable", data["warnings"])

    def test_missing_empty_invalid_sources_are_unknown_not_zero(self):
        self.source.rmdir()
        data = self.compute()
        self.assertEqual(data["sourceStatus"], "missing")
        self.assertIsNone(data["files"][0]["estimatedSessions"])
        self.source.mkdir()
        data = self.compute()
        self.assertEqual(data["sourceStatus"], "partial")
        self.assertIn("empty_source", data["warnings"])
        (self.source / "bad.jsonl").write_text("not json\n")
        data = self.compute()
        self.assertEqual(data["sourceStatus"], "partial")
        self.assertIsNone(data["files"][0]["estimatedSessions"])

    def test_partial_last_line_does_not_erase_good_evidence(self):
        path = self.log("partial.jsonl", self.event("session_meta", {
            "id": "s", "cwd": str(self.repo)}))
        with path.open("a") as stream:
            stream.write('{"type":')
        data = self.compute()
        self.assertEqual(data["files"][0]["estimatedSessions"], 1)
        self.assertEqual(data["sourceStatus"], "partial")
        self.assertIn("truncated_jsonl", data["warnings"])

    def test_bad_timestamp_never_falls_back_to_log_mtime(self):
        meta = self.event("session_meta", {"id": "s", "cwd": str(self.repo)},
                          self.now - 40 * 86400)
        event = self.event("event_msg", {"text": str(self.path)})
        event["timestamp"] = "not-time"
        path = self.log("time.jsonl", meta, event)
        os.utime(path, (self.now, self.now))
        data = self.compute()
        self.assertEqual(data["sessionsScanned"], 0)
        self.assertEqual(data["sourceStatus"], "partial")
        self.assertFalse(data["files"][0]["mentions"])
        self.assertIn("invalid_timestamp", data["warnings"])

    def test_unsupported_tool_files_remain_unknown(self):
        path = self.repo / "CLAUDE.md"
        path.write_text("claude")
        self.index["files"].append({"path": str(path), "kind": "claude"})
        self.log("one.jsonl", self.event("session_meta", {"id": "s", "cwd": str(self.repo)}))
        row = {r["path"]: r for r in self.compute()["files"]}[str(path)]
        self.assertFalse(row["supported"])
        self.assertEqual(row["sourceStatus"], "unsupported")
        self.assertIsNone(row["estimatedSessions"])

    def test_unknown_format_missing_id_and_unreadable_source(self):
        from unittest.mock import patch
        self.log("unknown.jsonl", self.event("future_schema", {"text": str(self.path)}))
        data = self.compute()
        self.assertEqual(data["sourceStatus"], "unsupported")
        self.assertIn("unsupported_event", data["warnings"])
        self.log("missing-id.jsonl", self.event("turn_context", {"cwd": str(self.repo)}))
        self.assertIn("missing_session_id", self.compute()["warnings"])
        original = os.scandir
        def denied(path):
            if Path(path) == self.source:
                raise PermissionError("fixture denied")
            return original(path)
        with patch("os.scandir", side_effect=denied):
            data = self.compute()
        self.assertEqual(data["sourceStatus"], "unreadable")
        self.assertIsNone(data["files"][0]["estimatedSessions"])

    def test_historical_only_valid_logs_have_explicit_empty_window(self):
        self.log("old.jsonl", self.event("session_meta", {"id": "s", "cwd": str(self.repo)},
                                       self.now - 40 * 86400))
        data = self.compute()
        self.assertEqual(data["sourceStatus"], "available")
        self.assertEqual(data["sessionsScanned"], 0)
        self.assertEqual(data["files"][0]["estimatedSessions"], 0)
        self.assertIn("no_sessions_in_window", data["warnings"])

    def test_validate_days(self):
        for days in (0, 366, True, "30", 1.5):
            with self.subTest(days=days), self.assertRaises(ValueError):
                self.compute(days=days)

    def cache_compute(self, **kwargs):
        return usage.compute_usage(self.index, self.source, self.home,
                                   now=self.now, cache_dir=self.root / ".agentatlas", **kwargs)

    def test_cache_reuses_aggregate_never_raw_logs(self):
        from unittest.mock import patch
        self.log("one.jsonl", self.event("session_meta", {"id": "s", "cwd": str(self.repo)}),
                 self.event("event_msg", {"text": "PRIVATE MESSAGE DO NOT PERSIST"}))
        with patch.object(usage, "collect_codex", wraps=usage.collect_codex) as collect:
            first = self.cache_compute()
            second = self.cache_compute()
        self.assertEqual(first, second)
        self.assertEqual(collect.call_count, 1)
        files = list((self.root / ".agentatlas").glob("*.json"))
        self.assertEqual(len(files), 1)
        cached = json.loads(files[0].read_text())
        self.assertEqual(cached["schemaVersion"], 2)
        self.assertNotIn("PRIVATE MESSAGE", files[0].read_text())
        self.assertFalse(files[0].stat().st_mode & 0o077)

    def test_cache_invalidates_log_index_live_layout_and_parser(self):
        from unittest.mock import patch
        log = self.log("one.jsonl", self.event("session_meta", {"id": "s", "cwd": str(self.repo)}))
        with patch.object(usage, "collect_codex", wraps=usage.collect_codex) as collect:
            self.cache_compute()
            self.index["files"][0]["sha"] = "index-changed"
            self.cache_compute()
            self.path.write_text("live-changed")
            self.cache_compute()
            # Unindexed override changes priority and must invalidate immediately.
            (self.repo / "AGENTS.override.md").write_text("override")
            result = self.cache_compute()
            self.assertEqual(result["files"][0]["estimatedSessions"], 0)
            with log.open("a") as stream:
                stream.write(json.dumps(self.event("event_msg", {"text": str(self.path)})) + "\n")
            self.assertEqual(self.cache_compute()["files"][0]["mentions"], 1)
            with patch.object(usage, "PARSER_VERSION", "next-parser"):
                self.cache_compute()
            self.cache_compute(days=31)
        self.assertEqual(collect.call_count, 7)

    def test_cache_tracks_nonindexed_cwd_overrides(self):
        from unittest.mock import patch
        child = self.repo / "nested"
        child.mkdir()
        self.log("one.jsonl", self.event("session_meta", {"id": "s", "cwd": str(child)}))
        with patch.object(usage, "collect_codex", wraps=usage.collect_codex) as collect:
            self.cache_compute()
            (child / "AGENTS.override.md").write_text("new local instructions")
            self.cache_compute()
        self.assertEqual(collect.call_count, 2)

    def test_cache_singleflight(self):
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch
        import threading
        self.log("one.jsonl", self.event("session_meta", {"id": "s", "cwd": str(self.repo)}))
        barrier = threading.Barrier(6)
        def request(_):
            barrier.wait(timeout=5)
            return self.cache_compute()
        with patch.object(usage, "collect_codex", wraps=usage.collect_codex) as collect:
            with ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(request, range(6)))
        self.assertEqual(collect.call_count, 1)
        self.assertTrue(all(value == results[0] for value in results))

    def test_legacy_and_corrupt_cache_do_not_override_missing_source(self):
        cache = self.root / ".agentatlas"
        cache.mkdir()
        (cache / ".usage-cache.json").write_text(json.dumps({"schemaVersion": 1,
            "files": [{"path": str(self.path), "estimatedSessions": 99}]}))
        self.source.rmdir()
        data = self.cache_compute()
        self.assertEqual(data["sourceStatus"], "missing")
        self.assertIsNone(data["files"][0]["estimatedSessions"])
        for path in cache.glob("usage-v2*.json"):
            path.write_text("{corrupt")
        self.assertEqual(self.cache_compute()["sourceStatus"], "missing")

    def test_cache_ttl_and_bypass(self):
        from unittest.mock import patch
        self.log("one.jsonl", self.event("session_meta", {"id": "s", "cwd": str(self.repo)}))
        with patch.object(usage, "collect_codex", wraps=usage.collect_codex) as collect:
            self.cache_compute()
            self.cache_compute(use_cache=False)
            self.now += 601
            self.cache_compute()
        self.assertEqual(collect.call_count, 3)

    def test_append_during_scan_is_partial_and_does_not_chase_appends(self):
        log = self.log("live.jsonl", self.event("session_meta", {"id": "s", "cwd": str(self.repo)}))
        def resolver(cwd):
            with log.open("a") as stream:
                stream.write(json.dumps(self.event("event_msg", {"text": str(self.path)})) + "\n")
            return {"files": [{"path": str(self.path)}]}
        data = usage.collect_codex(self.source, [self.path], self.now - 86400, self.now, resolver)
        self.assertEqual(data["sourceStatus"], "partial")
        self.assertIn("unstable_snapshot", data["warnings"])
        self.assertEqual(data["files"][0]["estimatedSessions"], 1)
        self.assertEqual(data["files"][0]["mentions"], 0)

    def test_oversized_event_and_all_unreadable_logs_are_explicit(self):
        from unittest.mock import patch
        import builtins
        log = self.log("one.jsonl", self.event("session_meta", {"id": "s", "cwd": str(self.repo)}))
        with patch.object(usage, "MAX_LINE_BYTES", 16):
            data = self.compute()
        self.assertEqual(data["sourceStatus"], "partial")
        self.assertIn("oversized_event", data["warnings"])
        original = builtins.open
        def denied(path, *args, **kwargs):
            if Path(path) == log:
                raise PermissionError("fixture denied")
            return original(path, *args, **kwargs)
        with patch("builtins.open", side_effect=denied):
            data = self.compute()
        self.assertEqual(data["sourceStatus"], "unreadable")
        self.assertIn("log_unreadable", data["warnings"])

    def test_worktree_keeps_original_paths_never_inflates_main(self):
        tree = self.root / "worktrees" / "same-name"
        tree.mkdir(parents=True)
        (tree / ".git").write_text("gitdir: /unverified\n")
        work_path = tree / "AGENTS.md"
        work_path.write_text("copy")
        self.index["files"].append({"path": str(work_path), "worktree": True})
        self.log("tree.jsonl", self.event("session_meta", {"id": "s", "cwd": str(tree)}))
        data = self.compute()
        rows = {r["path"]: r for r in data["files"]}
        self.assertEqual(rows[str(work_path)]["estimatedSessions"], 1)
        self.assertIsNone(rows[str(self.path)]["estimatedSessions"])
        self.assertIn("worktree_mapping_unverified", data["warnings"])

    def test_symlink_logs_and_directories_are_skipped_explicitly(self):
        outside = self.root / "external"
        outside.mkdir()
        secret = outside / "auth.jsonl"
        secret.write_text("SECRET FIXTURE")
        (self.source / "linked.jsonl").symlink_to(secret)
        (self.source / "directory").symlink_to(outside, target_is_directory=True)
        self.log("valid.jsonl", self.event("session_meta", {"id": "s", "cwd": str(self.repo)}))
        data = self.compute()
        self.assertEqual(data["sourceStatus"], "partial")
        self.assertIn("symlink_log_directory_skipped", data["warnings"])
        self.assertNotIn("SECRET FIXTURE", json.dumps(data))

    def test_cache_failure_and_size_limit_return_real_evidence(self):
        from unittest.mock import patch
        self.log("one.jsonl", self.event("session_meta", {"id": "s", "cwd": str(self.repo)}))
        with patch.object(usage.os, "replace", side_effect=OSError("fixture disk full")):
            data = self.cache_compute()
        self.assertEqual(data["files"][0]["estimatedSessions"], 1)
        self.assertIn("cache_unavailable", data["warnings"])
        self.assertEqual(list((self.root / ".agentatlas").glob("*.tmp")), [])
        with patch.object(usage, "MAX_CACHE_BYTES", 1):
            data = self.cache_compute()
        self.assertIn("cache_size_limit", data["warnings"])

    def test_cache_symlink_directory_refused(self):
        target = self.root / "outside-cache"
        target.mkdir()
        (self.root / ".agentatlas").symlink_to(target, target_is_directory=True)
        data = self.cache_compute()
        self.assertIn("cache_unavailable", data["warnings"])
        self.assertEqual(list(target.iterdir()), [])

    def test_metadata_without_valid_timestamp_is_not_trusted_for_exposure(self):
        event = self.event("session_meta", {"id": "s", "cwd": str(self.repo)})
        event["timestamp"] = None
        self.log("bad-meta.jsonl", event, self.event("event_msg", {"text": str(self.path)}))
        data = self.compute()
        self.assertEqual(data["sourceStatus"], "partial")
        # Metadata itself is still usable as context, but never a timed event.
        self.assertEqual(data["files"][0]["estimatedSessions"], 1)
        self.assertEqual(data["files"][0]["mentions"], 1)

    def test_snapshot_detects_new_log_during_collection(self):
        self.log("first.jsonl", self.event("session_meta", {"id": "s", "cwd": str(self.repo)}))
        def resolver(cwd):
            self.log("new.jsonl", self.event("session_meta", {"id": "s2", "cwd": str(self.repo)}))
            return {"files": [{"path": str(self.path)}]}
        data = usage.collect_codex(self.source, [self.path], self.now - 86400, self.now, resolver)
        self.assertEqual(data["sourceStatus"], "partial")
        self.assertIn("unstable_snapshot", data["warnings"])

    def test_evidence_detail_limit_does_not_truncate_session_counts(self):
        from unittest.mock import patch
        for index in range(4):
            self.log(str(index) + ".jsonl", self.event("session_meta", {
                "id": "s%d" % index, "cwd": str(self.repo)}))
        with patch.object(usage, "MAX_EVIDENCE", 2):
            data = self.compute()
        self.assertEqual(data["files"][0]["estimatedSessions"], 4)
        self.assertEqual(len(data["evidence"]), 2)
        self.assertEqual(data["sourceStatus"], "available")
        self.assertIn("evidence_details_limited", data["warnings"])
        self.assertEqual(set(data["evidence"][0]), {
            "tool", "sessionId", "log", "line", "timestamp", "path", "kind"})

    def test_disappearing_log_metadata_does_not_crash_or_become_available(self):
        from unittest.mock import patch
        log = self.log("gone.jsonl", self.event("session_meta", {"id": "s", "cwd": str(self.repo)}))
        original = usage._metadata
        def gone(path):
            return ["missing"] if Path(path) == log else original(path)
        with patch.object(usage, "_metadata", side_effect=gone):
            data = self.compute()
        self.assertEqual(data["sourceStatus"], "partial")
        self.assertIn("log_unreadable", data["warnings"])
        self.assertIsNone(data["files"][0]["estimatedSessions"])

    def test_log_replaced_by_symlink_after_manifest_is_not_opened(self):
        from unittest.mock import patch
        import builtins
        log = self.log("swapped.jsonl", self.event("session_meta", {"id": "s", "cwd": str(self.repo)}))
        target = self.root / "secret.json"
        target.write_text(json.dumps(self.event("session_meta", {"id": "secret", "cwd": str(self.repo)})) + "\n")
        original = builtins.open
        def swap(path, *args, **kwargs):
            if Path(path) == log and not log.is_symlink():
                log.unlink()
                log.symlink_to(target)
            return original(path, *args, **kwargs)
        with patch("builtins.open", side_effect=swap):
            data = self.compute()
        self.assertEqual(data["sessionsScanned"], 0)
        self.assertIn("log_unreadable", data["warnings"])


if __name__ == "__main__":
    unittest.main()
