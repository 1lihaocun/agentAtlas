"""Evaluation dataset loading and deterministic snapshot fingerprints (stdlib)."""
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from pathlib import Path

from atlas import sections


_MANIFEST_BYTES = 1024 * 1024
_MAX_ROWS = 1000
_GRADERS = frozenset(("usage-json-v1", "effective-links-v1", "effective-budget-v1"))
_SPLITS = frozenset(("train", "validation", "test"))
_ROW_KEYS = frozenset(("id", "group", "split", "agent", "task", "snapshot", "snapshotHash",
                       "cwd", "instructionStack", "target", "grader", "allowedWritePaths",
                       "critical", "provenance"))
_HASH = re.compile(r"[0-9a-f]{64}")
_SNAPSHOT_BYTES = 8 * 1024 * 1024
_MAX_FILES = 200
_EXTENSIONS = frozenset((".py", ".md", ".json", ".txt"))
_BLOCKED_NAMES = frozenset((".git", ".hg", ".svn", "node_modules", "__pycache__",
                            ".ssh", ".aws", ".azure", ".config", ".codex", ".claude", ".hermes"))
_SENSITIVE_NAME = re.compile(
    r"(?:^|[._-])(?:auth|oauth|credentials?|secrets?|tokens?|passwords?|config)(?:[._-]|$)"
    r"|^id_(?:rsa|dsa|ecdsa|ed25519)(?:[._-]|$)", re.IGNORECASE)
_CREDENTIAL_MATERIAL = re.compile(
    rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"
    rb"|(?:[\"']|\b)(?:[a-z0-9]+_)*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    rb"client[_-]?secret|token|password|aws[_-]?secret[_-]?access[_-]?key)[\"']?\s*[:=]\s*"
    rb"(?:[\"'][^\"']+[\"']|[^\s\"'{}\[\],;]+)",
    re.IGNORECASE)


def _relative(value, label, dot=False):
    _text(value, label)
    if dot and value == ".":
        return value
    if (value.startswith("/") or any(c in value for c in "\\:*?[]") or
            any(ord(c) < 32 or ord(c) == 127 for c in value) or
            any(part in ("", ".", "..") for part in value.split("/"))):
        raise ValueError(label + " must be a canonical safe POSIX relative path")
    return value


def _absolute(path):
    # Never resolve() or normpath(): either would hide symlink/.. ancestors.
    try:
        value = os.fspath(path)
    except TypeError as error:
        raise ValueError("path must be a string or PathLike") from error
    _text(value, "path")
    if ".." in value.split("/") or "\x00" in value:
        raise ValueError("parent traversal or NUL in path")
    return Path(value) if value.startswith("/") else Path.cwd() / value


@contextmanager
def _checked_open(path, directory=False):
    """Open each ancestor with O_NOFOLLOW, keeping resolution descriptor-relative."""
    path = _absolute(path)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = None
    try:
        fd = os.open("/", flags | os.O_DIRECTORY)
        parts = path.parts[1:]
        for index, name in enumerate(parts):
            is_dir = directory or index < len(parts) - 1
            child = os.open(name, flags | (os.O_DIRECTORY if is_dir else 0), dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if directory and not stat.S_ISDIR(info.st_mode):
            raise ValueError("expected directory")
        if not directory and not stat.S_ISREG(info.st_mode):
            raise ValueError("expected regular file")
        yield fd
    except (OSError, RecursionError) as error:
        raise ValueError("unsafe, missing, or unreadable filesystem path") from error
    finally:
        if fd is not None:
            os.close(fd)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_file(fd, limit):
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("only unaliased regular files are permitted")
    if before.st_size > limit:
        raise ValueError("file or aggregate byte limit exceeded")
    chunks = []
    size = 0
    while size <= limit:
        chunk = os.read(fd, min(65536, limit + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    if size > limit:
        raise ValueError("file or aggregate byte limit exceeded")
    if size != before.st_size or _identity(before) != _identity(os.fstat(fd)):
        raise ValueError("file changed while reading")
    return b"".join(chunks)


def _safe_name(name, directory):
    _relative(name, "snapshot entry")
    lower = name.casefold()
    if lower.startswith(".env") or lower in _BLOCKED_NAMES or _SENSITIVE_NAME.search(lower):
        raise ValueError("sensitive or generated snapshot entry is forbidden")
    if not directory and Path(name).suffix.casefold() not in _EXTENSIONS:
        raise ValueError("snapshot file extension is not allowlisted")


def _snapshot(root):
    files = {}
    directories = {"."}
    size = 0

    def visit(fd, prefix):
        nonlocal size
        before = os.fstat(fd)
        with os.scandir(fd) as entries:
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                directory = stat.S_ISDIR(info.st_mode)
                if not directory and not stat.S_ISREG(info.st_mode):
                    raise ValueError("symlinks and special files are forbidden")
                _safe_name(entry.name, directory)
                name = prefix + entry.name
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                child = os.open(entry.name, flags | (os.O_DIRECTORY if directory else 0), dir_fd=fd)
                try:
                    if _identity(info) != _identity(os.fstat(child)):
                        raise ValueError("snapshot entry changed during traversal")
                    if directory:
                        directories.add(name)
                        visit(child, name + "/")
                    else:
                        if len(files) >= _MAX_FILES:
                            raise ValueError("snapshot exceeds 200 files")
                        raw = _read_file(child, _SNAPSHOT_BYTES - size)
                        if _CREDENTIAL_MATERIAL.search(raw):
                            raise ValueError("credential material is forbidden in snapshots")
                        size += len(raw)
                        files[name] = raw
                finally:
                    os.close(child)
        if _identity(before) != _identity(os.fstat(fd)):
            raise ValueError("snapshot directory changed during traversal")

    with _checked_open(root, directory=True) as fd:
        visit(fd, "")
    if not files:
        raise ValueError("snapshot must contain at least one file")
    return files, directories


def _object(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError(label + " must contain exactly the required keys")


def _text(value, label):
    if type(value) is not str or not value.strip():
        raise ValueError(label + " must be a nonempty string")
    try:
        value.encode("utf-8")
    except UnicodeError as error:
        raise ValueError(label + " must be valid Unicode") from error


def _hash(value, label):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError(label + " must be 64 lowercase hexadecimal characters")


def _choice(value, choices, label):
    if type(value) is not str or value not in choices:
        raise ValueError("unsupported " + label)


def _schema(row, mode):
    _object(row, _ROW_KEYS, "record")
    for key in ("id", "group", "task", "snapshot", "cwd"):
        _text(row[key], key)
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", row["id"]) is None:
        raise ValueError("id must be a safe single artifact-directory name")
    _choice(row["split"], _SPLITS, "split")
    _choice(row["agent"], ("Codex",), "agent")
    _hash(row["snapshotHash"], "snapshotHash")
    if type(row["critical"]) is not bool:
        raise ValueError("critical must be boolean")
    for key in ("instructionStack", "allowedWritePaths"):
        if type(row[key]) is not list:
            raise ValueError(key + " must be a list")
    seen = set()
    for instruction in row["instructionStack"]:
        _object(instruction, ("relativePath", "sha256"), "instructionStack item")
        _text(instruction["relativePath"], "instruction relativePath")
        _hash(instruction["sha256"], "instruction sha256")
        if instruction["relativePath"] in seen:
            raise ValueError("duplicate instruction path")
        seen.add(instruction["relativePath"])
    seen = set()
    for name in row["allowedWritePaths"]:
        _text(name, "allowedWritePaths item")
        if name in seen:
            raise ValueError("duplicate allowed write path")
        seen.add(name)
    target = row["target"]
    _object(target, ("relativePath", "sectionId", "baseVersion"), "target")
    _text(target["relativePath"], "target relativePath")
    for key in ("sectionId", "baseVersion"):
        _hash(target[key], "target " + key)
    grader = row["grader"]
    _object(grader, ("id", "timeoutSeconds"), "grader")
    _choice(grader["id"], _GRADERS, "grader id")
    timeout = grader["timeoutSeconds"]
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise ValueError("timeoutSeconds must be an integer from 1 to 120")
    provenance = row["provenance"]
    _object(provenance, ("kind", "reviewed", "source"), "provenance")
    _choice(provenance["kind"], ("fixture", "reconstructed", "real"), "provenance kind")
    _text(provenance["source"], "provenance source")
    if type(provenance["reviewed"]) is not bool:
        raise ValueError("provenance reviewed must be boolean")
    if mode != "test":
        kinds = ("reconstructed", "real") if mode == "pilot" else ("real",)
        if not provenance["reviewed"] or provenance["kind"] not in kinds:
            raise ValueError(mode + " requires reviewed " + "/".join(kinds) + " provenance")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError("nonstandard JSON constant")


def _tree_hash(files):
    digest = hashlib.sha256()
    for name in sorted(files):
        encoded = name.encode("utf-8")
        raw = files[name]
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return digest.hexdigest()


def snapshot_hash(root) -> str:
    """Validate a snapshot and return its SHA-256 fingerprint.

    Sort POSIX-relative file paths lexicographically. For every file append
    u64be(path UTF-8 byte length), path bytes, u64be(content length), content.
    Directory entries themselves (including empty directories) are not hashed.
    Symlinks, hard links, special files, unsafe names and oversized trees fail
    closed with ValueError. No package __init__.py is required or imported.
    """
    files, _ = _snapshot(root)
    return _tree_hash(files)


def _check_snapshot(row, parent):
    snapshot_name = _relative(row["snapshot"], "snapshot")
    cwd = _relative(row["cwd"], "cwd", dot=True)
    for instruction in row["instructionStack"]:
        _relative(instruction["relativePath"], "instruction relativePath")
    target = row["target"]
    _relative(target["relativePath"], "target relativePath")
    for name in row["allowedWritePaths"]:
        _relative(name, "allowedWritePaths item")
    files, directories = _snapshot(parent / snapshot_name)
    if _tree_hash(files) != row["snapshotHash"]:
        raise ValueError("snapshotHash does not match snapshot bytes")
    if cwd not in directories:
        raise ValueError("cwd must be an existing snapshot directory")
    protected = {target["relativePath"]}
    for instruction in row["instructionStack"]:
        name = instruction["relativePath"]
        protected.add(name)
        if name not in files or hashlib.sha256(files[name]).hexdigest() != instruction["sha256"]:
            raise ValueError("instruction file missing or sha256 mismatch")
    if target["relativePath"] not in files:
        raise ValueError("target must be an existing snapshot file")
    doc = sections.snapshot(files[target["relativePath"]])
    if doc["version"] != target["baseVersion"]:
        raise ValueError("target baseVersion mismatch")
    section = next((s for s in doc["sections"] if s["id"] == target["sectionId"]), None)
    if section is None or section["optimizable"] is not True:
        raise ValueError("target section must exist and be optimizable")
    for name in row["allowedWritePaths"]:
        if name not in files:
            raise ValueError("allowedWritePaths must name exact existing regular files")
        if name in protected:
            raise ValueError("instructions and optimization target must not be writable")


def load_dataset(path, mode="pilot") -> list[dict]:
    """Validate a JSONL dataset and return unchanged records, in manifest order.

    Reject malformed/unsafe data with ValueError. Provenance is an explicit
    reviewer assertion, not evidence the loader can independently authenticate.
    """
    _choice(mode, ("test", "pilot", "baseline", "optimize"), "mode")
    path = _absolute(path)
    with _checked_open(path) as fd:
        raw = _read_file(fd, _MANIFEST_BYTES)
    try:
        lines = raw.decode("utf-8").split("\n")
        if lines[-1] == "":
            lines.pop()
    except UnicodeError as error:
        raise ValueError("manifest must be UTF-8") from error
    if not lines or len(lines) > _MAX_ROWS:
        raise ValueError("manifest must contain 1 to 1000 records")
    rows = []
    ids = set()
    groups = {}
    for number, line in enumerate(lines, 1):
        try:
            row = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_bad_constant)
            _schema(row, mode)
            if row["id"] in ids:
                raise ValueError("duplicate record id")
            if row["group"] in groups and groups[row["group"]] != row["split"]:
                raise ValueError("group crosses dataset splits")
            _check_snapshot(row, path.parent)
            ids.add(row["id"])
            groups[row["group"]] = row["split"]
        except (ValueError, RecursionError) as error:
            raise ValueError("manifest line %d: %s" % (number, error)) from error
        rows.append(row)
    if mode == "optimize" and (len(rows) < 20 or len(groups) < 3 or set(groups.values()) != _SPLITS):
        raise ValueError("optimize requires at least 20 reviewed real records, 3 groups, and all splits")
    return rows
