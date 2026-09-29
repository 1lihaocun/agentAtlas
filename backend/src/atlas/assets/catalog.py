"""Read-only discovery of local agent context, separate from instruction scanning.

Call ``discover(instruction_files=scan_result['files'])`` to retain existing
instruction coverage. Project roots are explicit repositories, never a request
to recursively search HOME. This module performs no writes and uses no network.

平台目录、裁剪例外、类别提示等平台知识统一来自 ``platforms`` 注册表
（一个平台一个子包）；本文件只保留通用策略与安全边界。
"""
import os
import hashlib
import json
import re
from pathlib import Path
import stat
import time
from collections import Counter


from atlas.platforms import (user_roots as _registry_user_roots,
                            project_roots as _registry_project_roots,
                            home_globs as _registry_home_globs,
                            instruction_platform as _registry_instruction_platform,
                            bootstrap_instructions as _registry_bootstrap,
                            category_dirs as _registry_category_dirs,
                            database_platforms as _registry_database_platforms)
from atlas.assets.policy import (TARGET_NAMES, PRUNE_DIRS, DEFAULT_DEPTH,
                                is_sensitive_path, _pruned, instruction_path_allowed)
from atlas.assets.identity import project_candidates
from atlas.files.access import open_regular as _open_regular

CATEGORIES = ("instruction", "memory", "skill", "reference", "command", "hook",
              "config", "session", "log", "other")


def _USER_ROOTS():
    return _registry_user_roots()


def _PROJECT_ROOTS():
    return _registry_project_roots()


def _INSTRUCTION_PLATFORM():
    return _registry_instruction_platform()


def _HOME_GLOBS():
    return _registry_home_globs()


_TEXT_SUFFIXES = {".md", ".markdown", ".mdc", ".txt", ".rst", ".json", ".jsonl",
                  ".ndjson", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".cfg",
                  ".conf", ".rules", ".log", ".csv", ".tsv", ".xml", ".html",
                  ".css", ".js", ".cjs", ".mjs", ".ts", ".py", ".sh", ".bash",
                  ".zsh", ".fish", ".ps1", ".rb", ".lua", ".sql", ".prompt",
                  ".jinja", ".jinja2", ".j2", ".tmpl", ".text", ".pbtxt"}

_EDIT_SUFFIXES = {".md", ".markdown", ".txt", ".mdc"}
_MEMORY_NAMES = {"memory.md", "user.md", "memory_summary.md", "raw_memories.md"}
_CONFIG_SUFFIXES = {".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf"}
_DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".vscdb"}
_CONFIG_LIMIT = 262144
_FIELD = re.compile(
    r'''(?:^|(?<=[\s{,\[]))(?:"([^"\n]+)"|'([^'\n]+)'|([A-Za-z_][A-Za-z0-9_.-]*))[ \t]*[:=]''',
    re.MULTILINE)


def _sensitive_field(key):
    name = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return (any(part in name for part in ("apikey", "secret", "password", "passwd", "credential", "privatekey", "authorization"))
            or name in {"auth", "oauth", "key", "token", "tokens", "env", "cookie", "cookies"}
            or name.endswith(("token", "clientkey")))


def is_sensitive_text(text):
    """Detect credential-bearing config fields without exposing their values.

    Conservative and format-tolerant (JSON/JSONC, YAML, TOML, INI). This is a
    safety filter, not a general-purpose guarantee that arbitrary prose has no
    secrets. Callers must also use is_sensitive_path and bound config reads.
    """
    if not isinstance(text, str):
        return True
    if (re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", text)
            or re.search(r"\b[a-z][a-z0-9+.-]{0,31}://[^\s/@:]+:[^\s/@]+@", text, re.I)):
        return True
    if any(_sensitive_field(next(group for group in match.groups() if group is not None))
           for match in _FIELD.finditer(text)):
        return True
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return False
    pending = [value]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if any(_sensitive_field(key) for key in value):
                return True
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return False


def _needs_config_check(path, category):
    return category == "config" or (category not in {"session", "log"} and path.suffix.lower() in _CONFIG_SUFFIXES)


def _safe_config(path, st):
    if st.st_size > _CONFIG_LIMIT:
        return False  # An unchecked tail cannot be declared safe.
    with _open_regular(path) as stream:
        raw = stream.read(_CONFIG_LIMIT + 1)
    if len(raw) > _CONFIG_LIMIT or b"\x00" in raw:
        return False
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return not is_sensitive_text(text)


def _category(path, relative, platform):
    parts = tuple(part.lower() for part in relative.parts[:-1])
    name = path.name.lower()
    platform_dirs = _registry_category_dirs(platform)
    if (any(part in {"logs", "log", "debug"} for part in parts) or ".log" in path.suffixes
            or name.startswith(("logs.", "logs_"))):
        return "log"
    if (any(part in {"sessions", "session", "archived_sessions", "chats", "chat-history", "conversations", "transcripts", "agent-transcripts"} for part in parts)
            or name.startswith(("history.", "session_index.", "sessions-index.", "prompt-history."))
            or (platform == "opencode" and "storage" in parts and any(part in {"message", "part"} for part in parts))
            or any(part in platform_dirs and platform_dirs[part] == "session" for part in parts)):
        return "session"
    if "hooks" in parts or "hook" in parts:
        return "hook"
    if (path.suffix.lower() in _CONFIG_SUFFIXES
            and path.stem.lower() in {"config", "settings", "settings.local", "mcp", "mcp_config"}):
        return "config"
    if _instruction(path) or "rules" in parts or (platform == "copilot" and name.endswith(".instructions.md")):
        return "instruction"
    if name in _registry_bootstrap(platform):
        return "instruction"
    if "references" in parts or "reference" in parts:
        return "reference"
    if "skills" in parts or "skill" in parts or name == "skill.md":
        return "skill"
    if "memory" in parts or "memories" in parts or name in _MEMORY_NAMES:
        return "memory"
    if path.suffix.lower() in _DATABASE_SUFFIXES and platform in _registry_database_platforms():
        return "session"
    if "commands" in parts or "command" in parts:
        return "command"
    if platform == "claude" and "projects" in parts and path.suffix in {".jsonl", ".json"}:
        return "session"
    if (path.suffix.lower() in _CONFIG_SUFFIXES
            or "config" in parts or "settings" in parts):
        return "config"
    return "other"


def _text_candidate(path, category):
    return (_instruction(path) or path.suffix.lower() in _TEXT_SUFFIXES
            or path.name.lower() in {"readme", "license", "notice"}
            or (category in {"session", "log"} and path.suffix.lower() in {"", ".tmp"}))


def _instruction(path):
    return path.name.lower() in TARGET_NAMES or path.suffix.lower() == ".mdc"


def read_text(entry, max_bytes=2097152):
    """Read a trusted inventory entry, rechecking safety on every access.

    Callers must resolve requests against their server-owned inventory first;
    an arbitrary client-supplied entry is NOT an authorization token. The byte
    count is the current full file size. For capped reads, ``sha256`` hashes only
    the returned raw prefix, explicitly labelled by ``sha256Scope='prefix'``.
    Config files must pass a complete bounded scan, even for tiny previews.
    Raises PermissionError for restricted paths/configs, ValueError for binary
    content or invalid limits, and OSError for missing/changing/unreadable files.
    """
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    if not isinstance(entry, dict) or not entry.get("searchable", False):
        raise PermissionError("This inventory entry cannot be previewed")
    path = Path(entry["path"])
    if (not path.is_absolute() or is_sensitive_path(path) or path.resolve() != path
            or not stat.S_ISREG(path.lstat().st_mode)):
        raise PermissionError("Path is restricted or is not a regular file")
    category = entry.get("category", "other")
    if not _text_candidate(path, category):
        raise ValueError("Unsupported non-text asset")
    config = _needs_config_check(path, category)
    with _open_regular(path) as stream:
        before = os.fstat(stream.fileno())
        if config:
            if before.st_size > _CONFIG_LIMIT:
                raise PermissionError("Configuration exceeds the safety-check limit")
            checked = stream.read(_CONFIG_LIMIT + 1)
            if len(checked) > _CONFIG_LIMIT or b"\x00" in checked:
                raise PermissionError("Configuration cannot be safely previewed")
            try:
                config_text = checked.decode("utf-8")
            except UnicodeDecodeError:
                raise PermissionError("Configuration is not valid UTF-8") from None
            if is_sensitive_text(config_text):
                raise PermissionError("Configuration contains sensitive fields")
            raw = checked[:max_bytes]
        else:
            raw = stream.read(max_bytes)
        after = os.fstat(stream.fileno())
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise OSError("File changed while being read; retry")
    if b"\x00" in raw or raw.startswith((b"%PDF-", b"\x89PNG", b"PK\x03\x04", b"\x7fELF")):
        raise ValueError("Binary content is unsupported; use an external viewer")
    try:
        text, replaced = raw.decode("utf-8"), False
    except UnicodeDecodeError:
        text, replaced = raw.decode("utf-8", errors="replace"), True
    truncated = after.st_size > len(raw)
    return {"content": text, "truncated": truncated, "bytes": after.st_size,
            "mtime": float(after.st_mtime), "mtimeNs": after.st_mtime_ns,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "sha256Scope": "prefix" if truncated else "file", "readBytes": len(raw),
            "decodeReplaced": replaced}


def discover(home=None, instruction_files=None, project_roots=None,
             scan_roots=None, max_depth=DEFAULT_DEPTH):
    """Return a JSON-serializable inventory; caller owns persistence.

    ``instruction_files`` is the original scan output list (extra metadata is
    retained). ``project_roots`` contains repository paths, not recursive search
    roots. Missing standard platform directories are normal, not scan errors.
    Symlink files/directories are not followed. Supplied instruction aliases
    targeting their own directory remain represented as restricted metadata.
    """
    home = Path(home or Path.home()).expanduser().resolve()
    user_roots = [(home / name, platform) for name, platform in _USER_ROOTS()]
    for pattern, platform in _HOME_GLOBS():
        user_roots.extend((path, platform) for path in sorted(home.glob(pattern)) if path.is_dir())
    originals = list(instruction_files or ())
    candidates = project_candidates(originals, project_roots, home=home,
                                    scan_roots=scan_roots, max_depth=max_depth,
                                    prune_dirs=PRUNE_DIRS)
    projects = {Path(row['path']) for row in candidates}
    limits = {row['path']: [source for source in row['sources'] if 'scanRoot' in source]
              for row in candidates
              if not any(source['kind'] == 'explicit' or
                         (scan_roots is None and source['kind'] == 'instruction_directory')
                         for source in row['sources'])}

    def within_limit(path, project, directory=False):
        if project not in limits:
            return True
        for source in limits[project]:
            boundary = Path(source['scanRoot'])
            if directory and path in boundary.parents:
                return True
            directory_path = path if directory else path.parent
            if (directory_path.is_relative_to(boundary)
                    and len(directory_path.relative_to(boundary).parts) <= source['maxDepth']):
                return True
        return False
    roots = [(root, platform, "user", "") for root, platform in user_roots]
    for project in sorted(projects, key=str):
        if project == home or project.resolve() != project:
            continue
        roots.extend((project / name, platform, "project", str(project))
                     for name, platform in _PROJECT_ROOTS())
    for candidate in candidates:
        for source in candidate['sources']:
            if source['kind'] == 'asset_directory':
                directory = Path(source['path']).parent
                roots.extend((directory / name, platform, 'project', candidate['path'])
                             for name, platform in _PROJECT_ROOTS())
    roots = sorted(set(roots), key=lambda row: (str(row[0]), row[1:]))
    entries = {}
    errors = 0

    def add(path, platform, scope="user", project="", original=None, root=None):
        nonlocal errors
        try:
            if original is None and not within_limit(path, project):
                return
            resolved = path.resolve()
            alias = resolved != path
            relative_policy = path.relative_to(root or home) if path.is_relative_to(root or home) else path
            if original is not None:
                if not instruction_path_allowed(path, root or home, platform):
                    return
            elif is_sensitive_path(path) or _pruned(relative_policy, platform):
                return
            if alias:
                if original is None or not _instruction(path) or resolved.parent != path.parent:
                    return
                path = resolved
            canonical = str(path)
            if original is None and canonical in entries:
                return
            st = path.lstat()
            if not stat.S_ISREG(st.st_mode):
                return
            relative = path.relative_to(root) if root else Path(path.name)
            natural_category = _category(path, relative, platform)
            category = "instruction" if original is not None else natural_category
            database = path.suffix.lower() in _DATABASE_SUFFIXES
            if not database and not _text_candidate(path, category):
                return
            if not alias and not database and _needs_config_check(path, category) and not _safe_config(path, st):
                return
            profile = "default"
            if platform == "hermes" and len(relative.parts) > 2 and relative.parts[0] == "profiles":
                profile = relative.parts[1]
            if platform == "openclaw" and root and root.name.startswith(".openclaw-"):
                profile = root.name[len(".openclaw-"):]
            if platform == "claude" and len(relative.parts) > 2 and relative.parts[0] == "projects":
                scope, project = "project", relative.parts[1]
            item = dict(entries.get(canonical, {}))
            item.update(original or {})
            item.update(path=canonical, name=path.name,
                        platform=_INSTRUCTION_PLATFORM().get(Path(original["path"]).name.lower() if original else path.name.lower(), platform),
                        category=category, scope=scope, profile=profile,
                        project=project, bytes=st.st_size, mtime=float(st.st_mtime),
                        mtimeNs=st.st_mtime_ns,
                        editable=(category in {"instruction", "memory", "skill", "reference", "command"}
                                  and (path.suffix.lower() in _EDIT_SUFFIXES or path.name.lower() in {".cursorrules", ".windsurfrules", ".clinerules"})
                                  and "scripts" not in relative.parts
                                  and natural_category not in {"config", "session", "log", "hook"}
                                  and not (platform == "hermes" and profile != "default")
                                  and not st.st_mode & 0o111), searchable=not database)
            if database:
                item["editable"] = False
                item["reason"] = "SQLite database: metadata only; inspect with an external read-only database viewer"
            if alias:
                item["editable"] = item["searchable"] = False
                item["reason"] = "Symlink instruction: metadata only; preview and editing are disabled"
            entries[canonical] = item
        except (OSError, RuntimeError):
            errors += 1

    def onerror(_error):
        nonlocal errors
        errors += 1

    for root, platform, scope, project in roots:
        if (within_limit(root, project, directory=True) and root.is_dir()
                and root.resolve() == root and not is_sensitive_path(root)):
            for base, dirs, names in os.walk(str(root), followlinks=False, onerror=onerror):
                dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()
                           and within_limit(Path(base) / name, project, directory=True)
                           and not is_sensitive_path(Path(base) / name)
                           and not _pruned((Path(base) / name).relative_to(root), platform)]
                for name in names:
                    path = Path(base) / name
                    add(path, platform, scope, project, root=root)
    for project in sorted(projects | {home}, key=str):
        if project.resolve() != project:
            continue
        try:
            with os.scandir(str(project)) as children:
                for child in children:
                    if child.name.lower() in TARGET_NAMES or (project == home and child.name == ".claude.json"):
                        add(Path(child.path), "claude" if child.name == ".claude.json" else _INSTRUCTION_PLATFORM().get(child.name.lower(), "shared"),
                            "user" if project == home else "project", "" if project == home else str(project))
        except FileNotFoundError:
            pass
        except OSError:
            errors += 1
        copilot = project / ".github/copilot-instructions.md"
        if copilot.is_file():
            add(copilot, "copilot", "project", str(project))
    for original in originals:
        path = Path(original["path"]).expanduser().absolute()
        platform, scope, project, matched_root = "shared", "project", str(path.parent), None
        for root, candidate, candidate_scope, candidate_project in sorted(roots, key=lambda row: len(row[0].parts), reverse=True):
            if root in path.parents:
                platform, scope, project, matched_root = candidate, candidate_scope, candidate_project, root
                break
        else:
            for candidate in sorted(projects, key=lambda p: len(p.parts), reverse=True):
                if candidate in path.parents:
                    project = str(candidate)
                    break
        if path.parent == home:
            scope, project = "user", ""
        add(path, platform, scope, project, original, matched_root)
    files = sorted(entries.values(), key=lambda item: item["path"].lower())
    category_counts = Counter(item["category"] for item in files)
    return {"generatedAt": int(time.time()), "totalFiles": len(files), "files": files,
            "projectCandidates": candidates, "home": str(home),
            "counts": {"categories": {key: category_counts[key] for key in CATEGORIES},
                       "platforms": dict(sorted(Counter(item["platform"] for item in files).items()))},
            "scanErrors": errors}
