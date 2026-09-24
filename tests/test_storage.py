"""Storage tests use only generated files beneath the runtime scratch root."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

try:
    from atlas.storage import Store, StorageProblem, Conflict, PreconditionRequired
except ModuleNotFoundError as error:
    if error.name != "atlas.storage":
        raise
    Store = None


def version(raw):
    return hashlib.sha256(raw).hexdigest()


class StorageTests(unittest.TestCase):
    def setUp(self):
        scratch = Path.home() / ".hermes/cache/scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="atlas-storage-", dir=str(scratch))
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / "state"
        self.now = 2_000_000_000
        self.assertIsNotNone(Store, "atlas.storage.Store must be implemented")

    def instruction(self, relative="repo/AGENTS.md", raw=b"## Rules\r\nOld.\r\n"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def test_invalid_action_and_snooze_rejected(self):
        store = Store(self.state)
        cases = [("delete", None), ("snooze", None), ("snooze", 0),
                 ("snooze", 366), ("snooze", "30"), ("snooze", True),
                 ("snooze", 1.5), ("keep", 30), ("reset", 1), ([], None)]
        for action, days in cases:
            with self.subTest(action=action, days=days), self.assertRaises(ValueError):
                store.decide("/fixture/AGENTS.md", "v1", action, self.now, days)
        for now in (float("nan"), float("inf"), True, "1"):
            with self.subTest(now=now), self.assertRaises(ValueError):
                store.decide("/fixture/AGENTS.md", "v1", "keep", now)
        self.assertIsNone(store.review("/fixture/AGENTS.md", "v1"))

    def test_snooze_and_reset_are_scoped_without_touching_file(self):
        path = self.instruction()
        before = (path.read_bytes(), path.stat())
        store = Store(self.state)
        store.decide(path, "v1", "keep", self.now)
        result = store.decide(path, "v2", "snooze", self.now, 365)
        self.assertEqual(result["until"], self.now + 365 * 86400)
        self.assertEqual(Store(self.state).review(path, "v2"), result)
        self.assertIsNone(store.decide(path, "v2", "reset", self.now))
        self.assertEqual(store.review(path, "v1")["action"], "keep")
        self.assertEqual((path.read_bytes(), path.stat()), before)

    def test_decision_survives_reopen(self):
        path = self.instruction()
        original = path.read_bytes()
        Store(self.state).decide(str(path), "v1", "keep", self.now)
        self.assertEqual(Store(self.state).review(str(path), "v1"), {
            "path": str(path), "version": "v1", "action": "keep",
            "until": None, "updatedAt": self.now,
        })
        self.assertEqual(path.read_bytes(), original)

    def test_decision_is_version_scoped(self):
        path = self.instruction()
        store = Store(self.state)
        store.decide(path, "v1", "keep", self.now)
        self.assertIsNone(store.review(path, "v2"))

    def test_private_state_and_database_configuration(self):
        self.state.mkdir(mode=0o755)
        store = Store(self.state)
        for path in (self.state, self.state / "backups"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(store.db_path.stat().st_mode), 0o600)
        with store._connection() as connection:
            for pragma, expected in (("journal_mode", "wal"), ("busy_timeout", 5000),
                                     ("foreign_keys", 1), ("user_version", 1), ("synchronous", 2)):
                self.assertEqual(connection.execute("PRAGMA " + pragma).fetchone()[0], expected)

    def test_future_schema_is_rejected_without_downgrade(self):
        Store(self.state)
        with sqlite3.connect(str(self.state / "state.sqlite3")) as connection:
            connection.execute("PRAGMA user_version = 42")
        with self.assertRaises(StorageProblem):
            Store(self.state)
        with sqlite3.connect(str(self.state / "state.sqlite3")) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 42)

    def test_symlink_state_and_internal_paths_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.state.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(StorageProblem):
            Store(self.state)
        self.assertEqual(list(outside.iterdir()), [])
        self.state.unlink()
        self.state.mkdir()
        for name in ("backups", "state.sqlite3", "state.sqlite3-wal", "state.sqlite3-shm"):
            link = self.state / name
            link.symlink_to(outside / "absent")
            with self.subTest(name=name), self.assertRaises(StorageProblem):
                Store(self.state)
            link.unlink()

    def save(self, store, path, raw=b"updated\n", expected=None):
        if expected is None:
            expected = version(path.read_bytes())
        return store.save(path, expected, raw, [str(path)], "test", self.now)

    def test_save_requires_matching_hash(self):
        path = self.instruction()
        store = Store(self.state)
        old_version = version(path.read_bytes())
        info = path.stat()
        path.write_bytes(b"external edit\n")
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        with self.assertRaises(Conflict) as caught:
            self.save(store, path, expected=old_version)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(path.read_bytes(), b"external edit\n")

    def test_save_validates_required_version_and_input_types(self):
        path = self.instruction()
        store = Store(self.state)
        for missing in (None, ""):
            with self.subTest(missing=missing), self.assertRaises(PreconditionRequired) as caught:
                store.save(path, missing, b"new", [path], "test", self.now)
            self.assertEqual(caught.exception.status, 428)
        for malformed in ("short", "g" * 64, "A" * 64, 123, []):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                store.save(path, malformed, b"new", [path], "test", self.now)
        for raw in ("text", None, bytearray(b"bytes")):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                store.save(path, version(path.read_bytes()), raw, [path], "test", self.now)

    def test_unindexed_path_denied(self):
        path = self.instruction()
        with self.assertRaises(StorageProblem) as caught:
            Store(self.state).save(path, version(path.read_bytes()), b"new", [], "test", self.now)
        self.assertEqual(caught.exception.status, 403)

    def test_symlinks_and_nonregular_targets_denied(self):
        path = self.instruction()
        store = Store(self.state)
        alias = path.with_name("alias")
        alias.symlink_to(path)
        dangling = path.with_name("dangling")
        dangling.symlink_to(path.with_name("absent"))
        fifo = path.with_name("fifo")
        os.mkfifo(fifo)
        parent_alias = self.root / "alias-dir"
        parent_alias.symlink_to(path.parent, target_is_directory=True)
        for unsafe in (alias, dangling, path.parent, fifo, parent_alias / path.name):
            with self.subTest(path=unsafe), self.assertRaises(StorageProblem) as caught:
                store.save(unsafe, version(b"anything"), b"new", [unsafe], "test", self.now)
            self.assertEqual(caught.exception.status, 403)

    def test_state_files_cannot_be_edit_targets(self):
        store = Store(self.state)
        path = self.state / "not-an-instruction"
        path.write_bytes(b"private")
        with self.assertRaises(StorageProblem) as caught:
            self.save(store, path)
        self.assertEqual(caught.exception.status, 403)

    def test_atomic_save_preserves_permissions_and_records_public_history(self):
        path = self.instruction(raw=b"\xef\xbb\xbf## A\r\noriginal\r\n")
        path.chmod(0o640)
        old = path.read_bytes()
        new = b"\xef\xbb\xbf## A\r\nchanged\r\n"
        store = Store(self.state)
        result = self.save(store, path, new)
        self.assertTrue(result["changed"])
        self.assertEqual(path.read_bytes(), new)
        self.assertEqual(Path(result["backup"]).read_bytes(), old)
        self.assertEqual(Path(result["backup"]).parent, self.state / "backups")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(Path(result["backup"]).stat().st_mode), 0o600)
        self.assertEqual(result["version"], version(new))
        self.assertEqual(result["sha256"], version(new))
        self.assertEqual(result["bytes"], len(new))
        self.assertEqual(result["mtime"], path.stat().st_mtime)
        history = store.edits(path)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["before_version"], version(old))
        self.assertEqual(history[0]["after_version"], version(new))
        self.assertEqual(history[0]["backup_path"], result["backup"])
        self.assertEqual(history[0]["status"], "committed")
        self.assertEqual(history[0]["source"], "test")
        self.assertEqual(history[0]["created_at"], self.now)
        self.assertEqual(store.edits(self.root / "other"), [])
        self.assertEqual(Store(self.state).edits(), history)

    def test_backups_do_not_collide(self):
        first = self.instruction("first/AGENTS.md", b"first")
        second = self.instruction("second/AGENTS.md", b"second")
        store = Store(self.state)
        a = self.save(store, first)
        b = self.save(store, second)
        self.assertNotEqual(a["backup"], b["backup"])
        self.assertEqual(Path(a["backup"]).read_bytes(), b"first")
        self.assertEqual(Path(b["backup"]).read_bytes(), b"second")
        c = self.save(store, first, b"first")
        d = self.save(store, first)
        self.assertEqual(len({r["backup"] for r in (a, b, c, d)}), 4)

    def test_noop_creates_no_edit(self):
        path = self.instruction()
        store = Store(self.state)
        before = path.stat()
        result = self.save(store, path, path.read_bytes())
        self.assertFalse(result["changed"])
        self.assertNotIn("backup", result)
        self.assertEqual(result["version"], version(path.read_bytes()))
        self.assertEqual(result["bytes"], before.st_size)
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(path.stat().st_ino, before.st_ino)
        self.assertEqual(store.edits(), [])
        self.assertEqual(list((self.state / "backups").iterdir()), [])

    def test_explicitly_authorized_assets_have_no_filename_filter(self):
        store = Store(self.state)
        for relative in (".cursorrules", "settings/custom.asset", "notes.md"):
            path = self.instruction(relative, b"old")
            self.assertTrue(self.save(store, path)["changed"])

    def test_database_failure_is_a_storage_problem(self):
        self.state.mkdir()
        (self.state / "state.sqlite3").write_bytes(b"not a sqlite database")
        with self.assertRaises(StorageProblem):
            Store(self.state)

    def test_backup_failure_leaves_source_unchanged(self):
        path = self.instruction()
        before = path.read_bytes()
        store = Store(self.state)
        with patch("atlas.storage.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(StorageProblem):
                self.save(store, path)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(store.edits(), [])
        self.assertEqual(list(store.backup_dir.iterdir()), [])

    def test_prepare_recovery_does_not_overwrite(self):
        store = Store(self.state)
        for content, expected in ((b"before", "aborted"), (b"after", "committed"),
                                  (b"external", "needs_review"), (None, "needs_review")):
            with self.subTest(content=content):
                path = self.instruction("recovery-" + expected + str(content) + "/AGENTS.md", b"before")
                with patch("atlas.storage.os.replace", side_effect=OSError("interrupted")):
                    with self.assertRaises(StorageProblem):
                        self.save(store, path, b"after")
                self.assertEqual(store.edits(path)[0]["status"], "prepared")
                self.assertEqual(list(path.parent.glob(".agentatlas-*.tmp")), [])
                if content is None:
                    path.unlink()
                else:
                    path.write_bytes(content)
                reopened = Store(self.state)
                self.assertEqual(reopened.edits(path)[0]["status"], expected)
                if content is None:
                    self.assertFalse(path.exists())
                else:
                    self.assertEqual(path.read_bytes(), content)

    def test_after_rename_audit_failure_is_explicit_and_recoverable(self):
        path = self.instruction()
        store = Store(self.state)
        before = path.read_bytes()
        with patch.object(store, "_finish_edit", side_effect=StorageProblem("database unavailable")):
            with self.assertRaises(StorageProblem) as caught:
                self.save(store, path, b"committed bytes")
        self.assertEqual(caught.exception.code, "write_committed_audit_pending")
        self.assertEqual(caught.exception.status, 500)
        self.assertTrue(caught.exception.changed)
        self.assertEqual(caught.exception.version, version(b"committed bytes"))
        self.assertEqual(path.read_bytes(), b"committed bytes")
        self.assertEqual(Path(caught.exception.backup).read_bytes(), before)
        self.assertEqual(store.edits(path)[0]["status"], "prepared")
        self.assertEqual(Store(self.state).edits(path)[0]["status"], "committed")

    def test_second_hash_check_detects_preserved_mtime_edit(self):
        path = self.instruction(raw=b"original")
        store = Store(self.state)
        original_backup = store._write_backup
        initial = path.stat()

        def edit_during_backup(*args):
            result = original_backup(*args)
            path.write_bytes(b"external")
            os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            return result

        with patch.object(store, "_write_backup", side_effect=edit_during_backup):
            with self.assertRaises(Conflict):
                self.save(store, path)
        self.assertEqual(path.read_bytes(), b"external")
        self.assertEqual(list(path.parent.glob(".agentatlas-*.tmp")), [])
        self.assertEqual(Store(self.state).edits(path)[0]["status"], "needs_review")

    def test_concurrent_store_instances_only_one_writer_succeeds(self):
        path = self.instruction()
        initial_version = version(path.read_bytes())
        first = Store(self.state)
        second = Store(self.state)
        gate = threading.Barrier(2)

        def save_concurrently(store, raw):
            gate.wait(timeout=5)
            try:
                return store.save(path, initial_version, raw, [path], "thread", self.now)
            except Conflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(save_concurrently, first, b"first")
            b = pool.submit(save_concurrently, second, b"second")
            results = [a.result(timeout=10), b.result(timeout=10)]
        self.assertEqual(results.count("conflict"), 1)
        self.assertEqual(len(first.edits()), 1)
        self.assertEqual(first.edits()[0]["status"], "committed")

    def test_temporary_creation_failure_preserves_error_and_source(self):
        path = self.instruction()
        before = path.read_bytes()
        store = Store(self.state)
        original_open = os.open

        def disk_full(name, *args, **kwargs):
            if os.fspath(name).endswith(".tmp"):
                raise OSError(28, "disk full")
            return original_open(name, *args, **kwargs)

        with patch("atlas.storage.os.open", side_effect=disk_full):
            with self.assertRaises(StorageProblem) as caught:
                self.save(store, path)
        self.assertEqual(caught.exception.status, 500)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(list(path.parent.glob(".agentatlas-*.tmp")), [])
        self.assertEqual(Store(self.state).edits(path)[0]["status"], "aborted")

    def test_missing_journal_after_replace_must_not_report_success(self):
        path = self.instruction()
        store = Store(self.state)
        original_finish = store._finish_edit

        def lose_journal(edit_id, status):
            with store._connection() as connection:
                connection.execute("DELETE FROM edits WHERE id = ?", (edit_id,))
            return original_finish(edit_id, status)

        with patch.object(store, "_finish_edit", side_effect=lose_journal):
            with self.assertRaises(StorageProblem) as caught:
                self.save(store, path)
        self.assertEqual(caught.exception.code, "write_committed_audit_pending")
        self.assertEqual(path.read_bytes(), b"updated\n")

    def test_directory_sync_failure_after_replace_is_not_success(self):
        path = self.instruction()
        store = Store(self.state)
        real_sync = os.fsync
        parent = path.parent.stat()

        def fail_target_directory(fd):
            info = os.fstat(fd)
            if (info.st_ino, info.st_dev) == (parent.st_ino, parent.st_dev):
                raise OSError("target directory fsync failed")
            return real_sync(fd)

        with patch("atlas.storage.os.fsync", side_effect=fail_target_directory):
            with self.assertRaises(StorageProblem) as caught:
                self.save(store, path)
        self.assertEqual(caught.exception.code, "write_committed_audit_pending")
        self.assertEqual(path.read_bytes(), b"updated\n")
        self.assertEqual(Store(self.state).edits(path)[0]["status"], "committed")

    def test_sqlite_prepare_failure_never_replaces_source(self):
        path = self.instruction()
        original = path.read_bytes()
        store = Store(self.state)
        with store._connection() as connection:
            connection.execute("CREATE TRIGGER fail_prepare BEFORE INSERT ON edits "
                               "BEGIN SELECT RAISE(FAIL, 'injected failure'); END")
        with self.assertRaises(StorageProblem):
            self.save(store, path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(store.edits(), [])
        self.assertEqual(list(path.parent.glob(".agentatlas-*.tmp")), [])

    def test_missing_temporary_during_failure_does_not_mask_storage_error(self):
        path = self.instruction()
        original = path.read_bytes()
        store = Store(self.state)

        def remove_temporary_then_fail(source, destination, **kwargs):
            os.unlink(source, dir_fd=kwargs["src_dir_fd"])
            raise OSError("replacement interrupted after external cleanup")

        with patch("atlas.storage.os.replace", side_effect=remove_temporary_then_fail):
            with self.assertRaises(StorageProblem):
                self.save(store, path)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(Store(self.state).edits(path)[0]["status"], "aborted")

    def test_optional_sqlite_sidecar_disappearance_is_not_failure(self):
        store = Store(self.state)
        original_lstat = Path.lstat

        def vanished_sidecar(path):
            if str(path).endswith("state.sqlite3-wal"):
                raise FileNotFoundError("another connection just removed the WAL")
            return original_lstat(path)

        with patch.object(Path, "exists", return_value=True), patch.object(Path, "lstat", vanished_sidecar):
            self.assertIsNone(store.review(self.root / "no-file", "v1"))

    def test_many_concurrent_decisions_and_independent_edits(self):
        store = Store(self.state)
        paths = [self.instruction("parallel-%d/AGENTS.md" % n) for n in range(16)]
        barrier = threading.Barrier(8)

        def work(path):
            barrier.wait(timeout=10)
            store.decide(path, "v1", "keep", self.now)
            self.assertEqual(store.review(path, "v1")["action"], "keep")
            return self.save(store, path)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(work, paths))
        self.assertTrue(all(result["changed"] for result in results))
        self.assertEqual(len(store.edits()), len(paths))

    def test_target_swapped_for_symlink_before_replace_is_rejected(self):
        path = self.instruction()
        outside = self.instruction("outside/AGENTS.md", b"outside")
        store = Store(self.state)
        original_backup = store._write_backup

        def swap_after_backup(*args):
            result = original_backup(*args)
            path.unlink()
            path.symlink_to(outside)
            return result

        with patch.object(store, "_write_backup", side_effect=swap_after_backup):
            with self.assertRaises(StorageProblem):
                self.save(store, path)
        self.assertTrue(path.is_symlink())
        self.assertEqual(outside.read_bytes(), b"outside")
        self.assertEqual(Store(self.state).edits(path)[0]["status"], "needs_review")

    def test_recovery_waits_for_inflight_writer_in_another_instance(self):
        path = self.instruction()
        store = Store(self.state)
        prepared = threading.Event()
        release = threading.Event()
        original_prepare = store._prepare

        def pause_after_prepare(*args):
            original_prepare(*args)
            prepared.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test did not release writer")

        with patch.object(store, "_prepare", side_effect=pause_after_prepare):
            with ThreadPoolExecutor(max_workers=2) as pool:
                writer = pool.submit(self.save, store, path)
                self.assertTrue(prepared.wait(timeout=5))
                reopening = pool.submit(Store, self.state)
                release.set()
                self.assertTrue(writer.result(timeout=10)["changed"])
                reopened = reopening.result(timeout=10)
        self.assertEqual(reopened.edits(path)[0]["status"], "committed")
