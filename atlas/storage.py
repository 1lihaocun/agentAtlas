"""Version-scoped review decisions and journaled local file edits.

SQLite connections are short lived: no connection is shared between threads.
"""
from contextlib import contextmanager
import errno
import hashlib
import math
import os
from pathlib import Path
import sqlite3
import stat
import threading
import uuid


class StorageProblem(Exception):
    def __init__(self, message, status=500, code="storage_error"):
        super().__init__(message)
        self.status = status
        self.code = code


class Conflict(StorageProblem):
    def __init__(self, message="File changed; reload before saving"):
        super().__init__(message, 409, "version_conflict")


class PreconditionRequired(StorageProblem):
    def __init__(self, message="A base content version is required"):
        super().__init__(message, 428, "precondition_required")


class Store:
    _locks_guard = threading.Lock()
    _locks = {}

    @classmethod
    def _lock_for(cls, path):
        with cls._locks_guard:
            return cls._locks.setdefault(str(path), threading.RLock())

    def __init__(self, state_dir):
        self.state_dir = Path(state_dir).absolute()
        self.backup_dir = self.state_dir / "backups"
        self.db_path = self.state_dir / "state.sqlite3"
        with self._lock_for(self.state_dir):
            try:
                for directory in (self.state_dir, self.backup_dir):
                    self._no_symlinks(directory)
                    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                    fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        if os.fstat(fd).st_uid != os.getuid():
                            raise StorageProblem("State directory must belong to this user", 403)
                        os.fchmod(fd, 0o700)
                    finally:
                        os.close(fd)
                self._check_state()
                fd = os.open(str(self.db_path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                finally:
                    os.close(fd)
            except OSError as error:
                raise StorageProblem("Cannot initialize private state: " + str(error)) from error
            with self._connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                schema = connection.execute("PRAGMA user_version").fetchone()[0]
                if schema > 1:
                    raise StorageProblem("State schema is newer than this application", 503,
                                         "unsupported_schema")
                connection.execute("""CREATE TABLE IF NOT EXISTS reviews (
                    path TEXT NOT NULL,
                    version TEXT NOT NULL,
                    action TEXT NOT NULL CHECK(action IN ('keep', 'snooze')),
                    until_ts REAL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(path, version)
                )""")
                connection.execute("""CREATE TABLE IF NOT EXISTS edits (
                    id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    before_version TEXT NOT NULL,
                    after_version TEXT NOT NULL,
                    backup_path TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('prepared', 'committed', 'aborted', 'needs_review'))
                )""")
                connection.execute("CREATE INDEX IF NOT EXISTS edits_path_time ON edits(path, created_at)")
                if schema == 0:
                    connection.execute("PRAGMA user_version = 1")
        self._recover()

    @staticmethod
    def _no_symlinks(path):
        if os.path.abspath(path) != os.path.realpath(path):
            raise StorageProblem("Symbolic links are not allowed", 403, "unsafe_path")

    def _check_state(self):
        for directory in (self.state_dir, self.backup_dir):
            self._no_symlinks(directory)
            info = directory.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o700):
                raise StorageProblem("State directories must be private", 403, "unsafe_state")
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.db_path) + suffix)
            self._no_symlinks(path)
            try:
                info = path.lstat()
            except FileNotFoundError:
                # WAL/SHM can disappear when another short-lived connection closes.
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid():
                raise StorageProblem("Unsafe state database file", 403, "unsafe_state")

    @contextmanager
    def _connection(self):
        connection = None
        try:
            self._check_state()
            connection = sqlite3.connect(str(self.db_path), timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            with connection:
                yield connection
        except sqlite3.Error as error:
            raise StorageProblem("State database failure: " + str(error)) from error
        except OSError as error:
            raise StorageProblem("State filesystem failure: " + str(error)) from error
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _path(path):
        path = os.fspath(path)
        if not isinstance(path, str) or not path or "\x00" in path:
            raise ValueError("path must be a nonempty text path")
        return os.path.abspath(path)

    def review(self, path, version):
        path = self._path(path)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT path, version, action, until_ts AS until, updated_at AS updatedAt "
                "FROM reviews WHERE path = ? AND version = ?", (path, version)
            ).fetchone()
            return dict(row) if row else None

    def decide(self, path, version, action, now, snooze_days=None):
        if action not in ("keep", "snooze", "reset"):
            raise ValueError("action must be keep, snooze, or reset")
        self._timestamp(now)
        if action == "snooze":
            if type(snooze_days) is not int or not 1 <= snooze_days <= 365:
                raise ValueError("snooze_days must be an integer from 1 to 365")
        elif snooze_days is not None:
            raise ValueError("snooze_days is only valid for snooze")
        path = self._path(path)
        until = now + snooze_days * 86400 if action == "snooze" else None
        with self._connection() as connection:
            if action == "reset":
                connection.execute("DELETE FROM reviews WHERE path = ? AND version = ?",
                                   (path, version))
            else:
                connection.execute(
                    "INSERT OR REPLACE INTO reviews(path, version, action, until_ts, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)", (path, version, action, until, now)
                )
        return None if action == "reset" else {
            "path": path, "version": version, "action": action,
            "until": until, "updatedAt": now,
        }

    @staticmethod
    def _timestamp(now):
        if type(now) not in (int, float) or not math.isfinite(now):
            raise ValueError("now must be a finite timestamp")

    def edits(self, path=None):
        """Return oldest-first journal records using SQLite's snake_case field names."""
        query = ("SELECT id, path, before_version, after_version, backup_path, "
                 "source, created_at, status FROM edits")
        parameters = ()
        if path is not None:
            query += " WHERE path = ?"
            parameters = (self._path(path),)
        with self._connection() as connection:
            return [dict(row) for row in connection.execute(
                query + " ORDER BY created_at, rowid", parameters)]

    def _recover(self):
        """Reconcile unfinished metadata only; never restore or replace target bytes."""
        with self._connection() as connection:
            pending = [dict(row) for row in connection.execute(
                "SELECT id, path, before_version, after_version FROM edits WHERE status = 'prepared'")]
        for row in pending:
            path = row["path"]
            # Reopening a Store must not call an in-flight writer 'aborted'.
            with self._lock_for(path):
                with self._connection() as connection:
                    current_row = connection.execute("SELECT status FROM edits WHERE id = ?",
                                                     (row["id"],)).fetchone()
                if current_row is None or current_row["status"] != "prepared":
                    continue
                directory_fd = None
                try:
                    self._no_symlinks(path)
                    directory_fd = os.open(os.path.dirname(path),
                                           os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    raw, _ = self._read_target(path, directory_fd)
                    current = hashlib.sha256(raw).hexdigest()
                    status = ("committed" if current == row["after_version"] else
                              "aborted" if current == row["before_version"] else "needs_review")
                except (OSError, StorageProblem):
                    status = "needs_review"
                finally:
                    if directory_fd is not None:
                        os.close(directory_fd)
                self._finish_edit(row["id"], status)

    @staticmethod
    def _signature(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                info.st_ctime_ns, info.st_mode, info.st_uid, info.st_gid)

    def _read_target(self, path, directory_fd):
        self._no_symlinks(path)
        parent = os.stat(os.path.dirname(path), follow_symlinks=False)
        pinned = os.fstat(directory_fd)
        if (parent.st_dev, parent.st_ino) != (pinned.st_dev, pinned.st_ino):
            raise Conflict("Parent directory changed")
        name = os.path.basename(path)
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise StorageProblem("Only regular files can be edited", 403, "unsafe_path")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if self._signature(opened) != self._signature(info):
                raise Conflict("File changed while opening")
            raw = stream.read()
            after = os.fstat(stream.fileno())
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if self._signature(info) != self._signature(after) or self._signature(info) != self._signature(current):
            raise Conflict("File changed while reading")
        return raw, info

    @staticmethod
    def _sync_directory(path):
        fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _write_backup(self, path, before, raw):
        self._check_state()
        identity = hashlib.sha256(os.fsencode(path)).hexdigest()
        backup = self.backup_dir / (identity + "-" + before + "-" + uuid.uuid4().hex + ".bak")
        fd = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            self._sync_directory(self.backup_dir)
        except BaseException:
            backup.unlink(missing_ok=True)
            raise
        return str(backup)

    def _prepare(self, edit_id, path, before, after, backup, source, now):
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO edits(id, path, before_version, after_version, backup_path, "
                "source, created_at, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared')",
                (edit_id, path, before, after, backup, source, now))

    def _finish_edit(self, edit_id, status):
        with self._connection() as connection:
            changed = connection.execute(
                "UPDATE edits SET status = ? WHERE id = ? AND status = 'prepared'",
                (status, edit_id)).rowcount
            if changed != 1:
                raise StorageProblem("Prepared edit journal is missing or was changed")

    def save(self, path, expected_version, new_bytes, indexed_paths, source, now):
        """Replace one explicitly authorized regular file using a content precondition.

        Filename/category/editability policy belongs to the caller. These process-wide
        path locks coordinate Store instances, not uncooperative external editors.
        The second hash/stat check narrows, but cannot eliminate, their CAS window.
        """
        if expected_version is None or expected_version == "":
            raise PreconditionRequired()
        if (not isinstance(expected_version, str) or len(expected_version) != 64
                or any(c not in "0123456789abcdef" for c in expected_version)):
            raise ValueError("expected_version must be a lowercase SHA-256 digest")
        if not isinstance(new_bytes, bytes):
            raise ValueError("new_bytes must be bytes")
        if not isinstance(source, str) or not source:
            raise ValueError("source must be a nonempty string")
        self._timestamp(now)
        path = self._path(path)
        allowed = {self._path(item) for item in indexed_paths}
        if path not in allowed:
            raise StorageProblem("File is not in the authorized index", 403, "unindexed_path")
        if os.path.commonpath((path, str(self.state_dir))) == str(self.state_dir):
            raise StorageProblem("State files cannot be edit targets", 403, "unsafe_path")
        after = hashlib.sha256(new_bytes).hexdigest()
        with self._lock_for(path):
            directory_fd = None
            temporary = None
            replaced = False
            backup = None
            edit_id = uuid.uuid4().hex
            try:
                self._no_symlinks(path)
                directory_fd = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                old, info = self._read_target(path, directory_fd)
                before = hashlib.sha256(old).hexdigest()
                if before != expected_version:
                    raise Conflict()
                if old == new_bytes:
                    return {"changed": False, "bytes": len(old), "version": before,
                            "sha256": before, "mtime": info.st_mtime}
                backup = self._write_backup(path, before, old)
                self._prepare(edit_id, path, before, after, backup, source, now)
                name = ".agentatlas-" + uuid.uuid4().hex + ".tmp"
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory_fd)
                temporary = name
                with os.fdopen(fd, "wb") as stream:
                    stream.write(new_bytes)
                    stream.flush()
                    os.fchmod(stream.fileno(), stat.S_IMODE(info.st_mode))
                    os.fsync(stream.fileno())
                    written = os.fstat(stream.fileno())
                current, current_info = self._read_target(path, directory_fd)
                if (hashlib.sha256(current).hexdigest() != before
                        or self._signature(current_info) != self._signature(info)):
                    raise Conflict("File changed during save; reload before retrying")
                os.replace(temporary, os.path.basename(path),
                           src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
                replaced = True
                temporary = None
                os.fsync(directory_fd)
                self._finish_edit(edit_id, "committed")
                return {"changed": True, "backup": backup, "bytes": len(new_bytes),
                        "version": after, "sha256": after, "mtime": written.st_mtime}
            except Exception as error:
                if replaced:
                    problem = StorageProblem(
                        "File replacement completed, but durability/audit confirmation failed; "
                        "reload the file and do not blindly retry", 500, "write_committed_audit_pending")
                    problem.changed = True
                    problem.version = after
                    problem.backup = backup
                    problem.edit_id = edit_id
                    raise problem from error
                if isinstance(error, OSError):
                    status = 404 if error.errno == errno.ENOENT else (
                        403 if error.errno in (errno.EACCES, errno.EPERM, errno.ELOOP) else 500)
                    raise StorageProblem("File save failed: " + str(error), status) from error
                raise
            finally:
                if directory_fd is not None:
                    try:
                        if temporary is not None:
                            try:
                                os.unlink(temporary, dir_fd=directory_fd)
                            except FileNotFoundError:
                                pass
                            except OSError as error:
                                raise StorageProblem("Save failed and temporary cleanup failed: "
                                                     + str(error)) from error
                    finally:
                        os.close(directory_fd)
