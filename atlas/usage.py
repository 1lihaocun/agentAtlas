"""Conservative Codex evidence from caller-selected retained JSONL logs.

Only event timestamps define the window. No read instrumentation is implemented:
mentions (including successful shell output) are never confirmed reads. Raw log
content stays transient and is never returned or cached.
"""
from datetime import datetime
from pathlib import Path
import hashlib
import json
import math
import os
import re
import stat
import tempfile
import threading
import time

try:
    from .effective import ASSUMPTIONS, find_project_root, resolve_codex
except ImportError:  # python3 atlas/serve.py
    from effective import ASSUMPTIONS, find_project_root, resolve_codex

SCHEMA_VERSION = 2
PARSER_VERSION = "codex-retained-v2.2"
SUPPORTED_NAMES = frozenset(("AGENTS.md", "AGENTS.override.md"))
EVENT_TYPES = frozenset(("session_meta", "turn_context", "response_item", "event_msg"))
MAX_LINE_BYTES = 2 * 1024 * 1024
MAX_LOG_FILES = 100000
MAX_EVENTS = 2000000
MAX_EVIDENCE = 2000
MAX_ARGUMENT_BYTES = 65536
MAX_MENTION_NODES = 10000
MAX_MENTION_DEPTH = 32
_PATH_TOKEN = re.compile(r'''(?<![\w/])(/[^\s"'`\\<>{}\[\](),;]+)''')
_QUOTED_PATH = re.compile(r'''["'`](/[^\n"'`]+)["'`]''')


def _metadata(path):
    try:
        value = os.stat(path)
        return [value.st_size, value.st_mtime_ns, value.st_ctime_ns,
                value.st_ino, value.st_dev, value.st_mode]
    except FileNotFoundError:
        return ["missing"]
    except OSError:
        return ["unreadable"]


def _manifest(source_root):
    """List regular JSONL files only; do not follow log symlinks into secrets."""
    root = Path(source_root)
    warnings = set()
    try:
        if not stat.S_ISDIR(root.stat().st_mode):
            return [], "unreadable", {"source_not_directory"}
    except FileNotFoundError:
        return [], "missing", {"source_missing"}
    except OSError:
        return [], "unreadable", {"source_unreadable"}
    paths = []
    def onerror(error):
        warnings.add("source_unreadable")
    for directory, dirs, names in os.walk(str(root), onerror=onerror, followlinks=False):
        safe_dirs = []
        for name in dirs:
            if (Path(directory) / name).is_symlink():
                warnings.add("symlink_log_directory_skipped")
            else:
                safe_dirs.append(name)
        dirs[:] = sorted(safe_dirs)
        for name in sorted(names):
            if not name.endswith(".jsonl"):
                continue
            path = Path(directory) / name
            try:
                mode = path.lstat().st_mode
                if not stat.S_ISREG(mode):
                    warnings.add("nonregular_log_skipped")
                    continue
                metadata = _metadata(path)
                if not isinstance(metadata[0], int):
                    warnings.add("log_unreadable")
                    continue
                paths.append((str(path), metadata))
            except OSError:
                warnings.add("log_unreadable")
            if len(paths) >= MAX_LOG_FILES:
                warnings.add("source_file_limit")
                return paths, "partial", warnings
    if not paths:
        if "source_unreadable" in warnings:
            return [], "unreadable", warnings
        warnings.add("empty_source")
        return [], "partial", warnings
    return paths, "partial" if warnings else "available", warnings


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO-8601 string with timezone")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone missing")
    value = parsed.timestamp()
    if not math.isfinite(value):
        raise ValueError("nonfinite timestamp")
    return value


def _mentions(payload, aliases, warnings):
    """Extract tokens once per payload, then indexed set/dict membership.

    Accept exact string paths, quoted paths containing spaces, and unquoted
    whitespace-delimited paths. Do not infer relative paths or prefix matches.
    """
    found = set()
    stack = [(payload, 0)]
    visited, argument_bytes = 0, 0
    while stack:
        value, depth = stack.pop()
        visited += 1
        if visited > MAX_MENTION_NODES:
            warnings.add("mention_extraction_limit")
            break
        if depth > MAX_MENTION_DEPTH:
            warnings.add("mention_extraction_limit")
            continue
        if isinstance(value, dict):
            is_call = value.get("type") == "function_call"
            if is_call and "arguments" not in value:
                warnings.add("invalid_tool_arguments")
            for key, child in value.items():
                if is_call and key == "arguments":
                    if isinstance(child, str):
                        try:
                            # The cumulative budget also bounds recursively
                            # JSON-encoded nested calls. Never decode free text.
                            argument_bytes += len(child.encode("utf-8"))
                            if argument_bytes > MAX_ARGUMENT_BYTES:
                                warnings.add("tool_arguments_limit")
                                continue
                            child = json.loads(child)
                        except (ValueError, UnicodeError, RecursionError):
                            warnings.add("invalid_tool_arguments")
                            continue
                    if not isinstance(child, (dict, list)):
                        warnings.add("invalid_tool_arguments")
                        continue
                stack.append((child, depth + 1))
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
        elif isinstance(value, str):
            tokens = [value]
            tokens.extend(match.group(1) for match in _QUOTED_PATH.finditer(value))
            tokens.extend(match.group(1).rstrip(".!?:") for match in _PATH_TOKEN.finditer(value))
            for token in tokens:
                target = aliases.get(token)
                if target is not None:
                    found.add(target)
    return found


def _open_log(path, flags):
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError("nonregular log refused")
    return fd


def collect_codex(source_root, indexed_paths, since, now, resolver):
    """Return evidence rows; ``resolver(cwd)`` returns resolve_codex data.

    sessionsScanned counts distinct identified sessions with in-window supported
    events, even when cwd is unavailable. Missing IDs never become fake sessions.
    """
    aliases = {}
    for path in indexed_paths:
        canonical = str(Path(path).resolve())
        aliases[str(path)] = canonical
        aliases[canonical] = canonical
    indexed = set(aliases.values())
    estimates = {path: set() for path in indexed}
    mentions = {path: set() for path in indexed}
    last = {path: None for path in indexed}
    sessions, evidence, evidence_keys = set(), [], set()
    paths, status, warnings = _manifest(source_root)
    initial_warnings = set(warnings)
    supported_events = 0
    event_count = 0
    resolved = {}

    def add_evidence(path, kind, sid, log, line, timestamp):
        key = (path, kind, sid)
        if key in evidence_keys:
            return
        if len(evidence) >= MAX_EVIDENCE:
            warnings.add("evidence_details_limited")
            return
        evidence_keys.add(key)
        evidence.append({"tool": "Codex", "sessionId": sid, "log": log,
                         "line": line, "timestamp": timestamp, "path": path, "kind": kind})

    for path, before in paths:
        sid, cwd = None, None
        relative = os.path.relpath(path, source_root)
        try:
            with open(path, "rb", opener=_open_log) as stream:
                line_number = 0
                # Initial size is the snapshot boundary; appends cannot make this
                # scan chase a live producer forever.
                remaining = before[0]
                while remaining > 0:
                    raw = stream.readline(min(MAX_LINE_BYTES + 1, remaining))
                    if not raw:
                        break
                    remaining -= len(raw)
                    line_number += 1
                    event_count += 1
                    if event_count > MAX_EVENTS:
                        warnings.add("event_limit")
                        break
                    if len(raw) > MAX_LINE_BYTES:
                        warnings.add("oversized_event")
                        while remaining > 0 and not raw.endswith(b"\n"):
                            raw = stream.readline(min(MAX_LINE_BYTES + 1, remaining))
                            if not raw:
                                break
                            remaining -= len(raw)
                        continue
                    if not raw.strip():
                        continue
                    try:
                        event = json.loads(raw)
                    except (ValueError, UnicodeError, RecursionError):
                        warnings.add("invalid_jsonl" if raw.endswith(b"\n") else "truncated_jsonl")
                        continue
                    if not isinstance(event, dict) or not isinstance(event.get("payload"), dict):
                        warnings.add("unsupported_event")
                        continue
                    kind, payload = event.get("type"), event["payload"]
                    if not isinstance(kind, str) or kind not in EVENT_TYPES:
                        warnings.add("unsupported_event")
                        continue
                    supported_events += 1
                    # Metadata must survive the window filter (long-lived sessions).
                    if kind == "session_meta":
                        sid = payload.get("id")
                        if not isinstance(sid, str) or not sid or len(sid) > 256:
                            sid = None
                        cwd = payload.get("cwd")
                    elif kind == "turn_context" and "cwd" in payload:
                        cwd = payload["cwd"]
                    try:
                        timestamp = _timestamp(event.get("timestamp"))
                    except (ValueError, OverflowError, OSError):
                        warnings.add("invalid_timestamp")
                        continue
                    if not since <= timestamp <= now:
                        continue
                    if sid is None:
                        warnings.add("missing_session_id")
                        continue
                    sessions.add(sid)
                    for target in _mentions(payload, aliases, warnings):
                        mentions[target].add(sid)
                        add_evidence(target, "mention", sid, relative, line_number, timestamp)
                    if not isinstance(cwd, str) or not cwd or not os.path.isabs(cwd):
                        warnings.add("missing_cwd")
                        continue
                    if cwd not in resolved:
                        try:
                            if not Path(cwd).is_dir():
                                warnings.add("cwd_unavailable")
                                resolved[cwd] = None
                            else:
                                resolved[cwd] = resolver(cwd)
                        except (OSError, ValueError, RuntimeError):
                            warnings.add("resolver_unavailable")
                            resolved[cwd] = None
                    stack = resolved[cwd]
                    if stack is None:
                        continue
                    # Resolver warnings are codes, not instruction content.
                    for warning in stack.get("warnings", []):
                        warnings.add(warning.split(":", 1)[0])
                    if stack.get("truncated"):
                        warnings.add("instruction_byte_limit")
                    for instruction in stack["files"]:
                        target = instruction["path"]
                        if target in indexed and Path(target).name in SUPPORTED_NAMES:
                            estimates[target].add(sid)
                            last[target] = max(last[target] or timestamp, timestamp)
                            add_evidence(target, "estimated", sid, relative, line_number, timestamp)
        except OSError:
            warnings.add("log_unreadable")
        if _metadata(path) != before:
            warnings.add("unstable_snapshot")
        if event_count > MAX_EVENTS:
            break
    after_paths, after_status, after_warnings = _manifest(source_root)
    if (after_paths != paths or after_status != status
            or after_warnings != initial_warnings):
        warnings.add("unstable_snapshot")
        status = "partial"
    if status == "available" and not supported_events:
        if "log_unreadable" in warnings and warnings <= {"log_unreadable"}:
            status = "unreadable"
        else:
            status = "unsupported" if "unsupported_event" in warnings else "partial"
        warnings.add("no_supported_events")
    elif status == "available" and (warnings - initial_warnings - {"evidence_details_limited"}):
        status = "partial"
    if not sessions:
        warnings.add("no_sessions_in_window")
    rows = []
    for path in sorted(indexed):
        supported = Path(path).name in SUPPORTED_NAMES
        row_status = status if supported else "unsupported"
        estimated = len(estimates[path]) if supported else None
        if not estimated and row_status != "available":
            estimated = None
        counts = {"estimatedSessions": estimated, "confirmedReads": None,
                  "mentions": len(mentions[path]), "lastEffective": last[path],
                  "readStatus": "not_instrumented", "sourceStatus": row_status}
        rows.append(dict(counts, path=path, supported=supported, byTool={"Codex": dict(counts)}))
    return {"files": rows, "sessionsScanned": len(sessions), "sourceStatus": status,
            "warnings": sorted(warnings), "observationScope": "retained_logs_only",
            "evidence": evidence, "readStatus": "not_instrumented",
            "logsScanned": len(paths), "windowStart": since, "windowEnd": now}


CACHE_TTL = 600
MAX_CACHE_BYTES = 8 * 1024 * 1024
MAX_DEPENDENCIES = 100000
# Bounded striped locks provide process-local singleflight without a leaking
# per-fingerprint lock registry. No claim of cross-process mutual exclusion.
_CACHE_LOCKS = tuple(threading.Lock() for _ in range(64))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("utf-8")).hexdigest()


def _fingerprint(index, source_root, codex_home, days):
    manifest, status, warnings = _manifest(source_root)
    return _digest({"schema": SCHEMA_VERSION, "parser": PARSER_VERSION, "tool": "Codex",
                    "days": days, "index": index,
                    "liveIndex": [[r["path"], str(Path(r["path"]).resolve()), _metadata(r["path"])]
                                  for r in index["files"]],
                    "source": str(Path(source_root).resolve()), "manifest": manifest,
                    "sourceStatus": status, "warnings": sorted(warnings),
                    "home": str(Path(codex_home).resolve())})


def _dependencies(cwd, codex_home):
    """Stat only instruction candidates / .git markers, never config contents.

    Include absent priority candidates and every ancestor marker: creation of an
    unindexed override or .git root must invalidate current-layout estimates.
    """
    result = set()
    home = Path(codex_home).absolute()
    for name in SUPPORTED_NAMES:
        result.add(str(home / name))
    current = Path(cwd).absolute()
    result.add(str(current))  # Detect deletion or retargeting of cwd.
    for directory in (current,) + tuple(current.parents):
        result.add(str(directory / ".git"))
        for name in SUPPORTED_NAMES:
            result.add(str(directory / name))
    return result


def _mark_partial(data, warning):
    data["sourceStatus"] = "partial"
    data["warnings"] = sorted(set(data["warnings"]) | {warning})
    for row in data["files"]:
        if row["supported"]:
            row["sourceStatus"] = "partial"
            if not row["estimatedSessions"]:
                row["estimatedSessions"] = None
            row["byTool"]["Codex"]["sourceStatus"] = "partial"
            row["byTool"]["Codex"]["estimatedSessions"] = row["estimatedSessions"]


def _collect_usage(index, source_root, codex_home, days, now):
    dependencies = {}
    def resolver(cwd):
        for path in _dependencies(cwd, codex_home):
            if len(dependencies) < MAX_DEPENDENCIES:
                dependencies.setdefault(path, _metadata(path))
        return resolve_codex(cwd, find_project_root(cwd), codex_home)
    data = collect_codex(source_root, [row["path"] for row in index["files"]],
                         now - days * 86400, now, resolver)
    data.update(schemaVersion=SCHEMA_VERSION, tool="Codex", days=days, generatedAt=now,
                assumptions=list(ASSUMPTIONS), parserVersion=PARSER_VERSION)
    if len(dependencies) >= MAX_DEPENDENCIES:
        _mark_partial(data, "dependency_limit")
    elif any(_metadata(path) != value for path, value in dependencies.items()):
        _mark_partial(data, "unstable_instruction_snapshot")
    return data, dependencies


def _read_cache(path, key, now):
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CACHE_BYTES:
                return None
            cached = json.loads(stream.read(MAX_CACHE_BYTES + 1))
        if (not isinstance(cached, dict) or cached.get("schemaVersion") != SCHEMA_VERSION
                or cached.get("key") != key or cached.get("parserVersion") != PARSER_VERSION):
            return None
        data = cached["data"]
        age = now - data["generatedAt"]
        if (not 0 <= age < CACHE_TTL or data["schemaVersion"] != SCHEMA_VERSION
                or data["tool"] != "Codex" or data["observationScope"] != "retained_logs_only"
                or not isinstance(data["files"], list)):
            return None
        dependencies = cached["dependencies"]
        if not isinstance(dependencies, dict) or len(dependencies) >= MAX_DEPENDENCIES:
            return None
        if any(not isinstance(p, str) or _metadata(p) != value
               for p, value in dependencies.items()):
            return None
        return data
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return None


def _write_cache(path, key, data, dependencies):
    envelope = {"schemaVersion": SCHEMA_VERSION, "parserVersion": PARSER_VERSION,
                "key": key, "data": data, "dependencies": dependencies}
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_CACHE_BYTES:
        return False
    fd, temporary = tempfile.mkstemp(prefix=".usage-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(path))
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return True


def compute_usage(index, source_root, codex_home, days=30, now=None, cache_dir=None, use_cache=True):
    """Version-two integration entry point; all source locations are explicit.

    Pass a private ``.agentatlas`` directory to enable the disk cache. With no
    cache_dir (or use_cache=False), no cache is read/written. Cached responses
    retain their original generatedAt/window, for at most 600 seconds. Errors
    are cached as errors; a TTL never converts partial observation into zero.
    """
    if type(days) is not int or not 1 <= days <= 365:
        raise ValueError("days must be an integer from 1 to 365")
    now = time.time() if now is None else now
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError("now must be a finite timestamp")
    if not isinstance(index, dict) or not isinstance(index.get("files"), list):
        raise ValueError("index must contain a files list")
    if not use_cache or cache_dir is None:
        return _collect_usage(index, source_root, codex_home, days, now)[0]
    directory = Path(cache_dir).absolute()
    path = directory / ("usage-v2-Codex-%d.json" % days)
    lock = _CACHE_LOCKS[int(_digest(str(path)), 16) % len(_CACHE_LOCKS)]
    with lock:
        key = _fingerprint(index, source_root, codex_home, days)
        safe_directory = not directory.is_symlink()
        if safe_directory:
            cached = _read_cache(path, key, now)
            if cached is not None:
                return cached
        data, dependencies = _collect_usage(index, source_root, codex_home, days, now)
        if _fingerprint(index, source_root, codex_home, days) != key:
            _mark_partial(data, "unstable_snapshot")
        if set(data["warnings"]) & {"unstable_snapshot", "unstable_instruction_snapshot", "dependency_limit"}:
            return data  # Never persist a transient snapshot as a stable cache hit.
        try:
            if not safe_directory:
                raise OSError("symlink cache directory")
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if directory.is_symlink() or not directory.is_dir():
                raise OSError("invalid cache directory")
            if not _write_cache(path, key, data, dependencies):
                data["warnings"] = sorted(set(data["warnings"]) | {"cache_size_limit"})
        except OSError:
            data["warnings"] = sorted(set(data["warnings"]) | {"cache_unavailable"})
        return data
