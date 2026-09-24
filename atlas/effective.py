"""Current-layout estimates, never proof of historical Codex injection.

No configuration or Git contents are read. All locations are caller supplied.
"""
import os
from pathlib import Path
import stat


ASSUMPTIONS = ("assumed_defaults", "configuration_not_read", "current_layout_not_historical",
               "raw_byte_budget_estimate_not_rendered_prompt")
_NAMES = ("AGENTS.override.md", "AGENTS.md")
MAX_INSPECTION_BYTES = 2 * 1024 * 1024


class _InspectionLimit(OSError):
    pass


def find_project_root(cwd):
    """Nearest .git directory OR file; never infer a different/main worktree.

    A missing cwd is not evidence of a current root. Permission failures propagate
    so callers can distinguish unknown from the legitimate no-project-root case.
    """
    current = Path(cwd).resolve()
    if not current.is_dir():
        return None
    for directory in (current,) + tuple(current.parents):
        try:
            mode = (directory / ".git").stat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(mode) or stat.S_ISREG(mode):
            return str(directory)
    return None


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _open_candidate(target):
    """Pin every canonical parent, refusing links at every open boundary."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(target.anchor, flags)
    try:
        for component in target.parts[1:-1]:
            child = os.open(component, flags, dir_fd=parent)
            os.close(parent)
            parent = child
        return os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                       dir_fd=parent)
    finally:
        os.close(parent)


def _candidate(path, read_limit=MAX_INSPECTION_BYTES):
    """Return the inspected canonical path and size; never reopen a live alias."""
    target = path.resolve()
    if (target.name.lower() in ("config.toml", "auth.json", "credentials.json")
            or target.name.lower().startswith(".env")):
        raise OSError("sensitive instruction target refused")
    try:
        before = target.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        return None
    if before.st_size > min(read_limit, MAX_INSPECTION_BYTES):
        raise _InspectionLimit("instruction exceeds bounded inspection budget")
    # Bound memory even for unexpectedly large instruction files. Decode chunks
    # incrementally so a multibyte character on a boundary is not rejected.
    import codecs
    decoder = codecs.getincrementaldecoder("utf-8")()
    nonempty = False
    fd = _open_candidate(target)
    try:
        if _identity(os.fstat(fd)) != _identity(before) or path.resolve() != target:
            raise OSError("instruction changed before inspection")
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                raise OSError("instruction shortened during inspection")
            remaining -= len(chunk)
            text = decoder.decode(chunk)
            nonempty = bool(text.strip()) or nonempty
        nonempty = bool(decoder.decode(b"", final=True).strip()) or nonempty
        if (_identity(os.fstat(fd)) != _identity(before)
                or _identity(os.stat(target, follow_symlinks=False)) != _identity(before)
                or path.resolve() != target):
            raise OSError("instruction changed during inspection")
    finally:
        os.close(fd)
    return (str(target), before.st_size) if nonempty else None


def resolve_codex(cwd, project_root, codex_home, fallback_names=(), max_bytes=32768):
    """Estimate a global + root-to-cwd stack, one non-blank file per directory.

    ``bytes`` is the raw byte budget assigned to the file, ``totalBytes`` its
    current size. A partial byte prefix (possibly splitting UTF-8) is NOT a
    section-level exposure claim. No instruction content is returned.
    """
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("max_bytes must be a nonnegative integer")
    fallback_names = tuple(fallback_names)
    for name in fallback_names:
        if (not isinstance(name, str) or not name or name in (".", "..")
                or "/" in name or "\\" in name
                or name.lower() in ("config.toml", "auth.json", "credentials.json")
                or name.lower().startswith(".env")):
            raise ValueError("fallback_names must be safe instruction basenames")
    cwd = Path(cwd).resolve()
    home = Path(codex_home).resolve()
    directories = [(home, "global")]
    root = None
    if project_root is None:
        directories.append((cwd, "project"))
    else:
        root = Path(project_root).resolve()
        relative = cwd.relative_to(root)  # ValueError when outside caller's root.
        directories.append((root, "project"))
        current = root
        for component in relative.parts:
            current = current / component
            directories.append((current, "project"))
    files, warnings = [], []
    remaining = max_bytes
    truncated = False
    seen = set()
    for directory, scope in directories:
        if remaining == 0:
            truncated = True
            break
        names = _NAMES if scope == "global" else _NAMES + fallback_names
        for name in names:
            path = directory / name
            try:
                # Up to three extra bytes permit a UTF-8 codepoint straddling
                # the raw prompt budget. Larger files remain explicitly unknown.
                inspected = _candidate(path, min(MAX_INSPECTION_BYTES, remaining + 3))
            except _InspectionLimit:
                warnings.append("instruction_inspection_limit:" + str(path))
                truncated = True
                break
            except (OSError, UnicodeError, RuntimeError):
                warnings.append("instruction_unreadable:" + str(path))
                # Cannot establish the priority winner; do not guess a fallback.
                break
            if inspected is None:
                continue
            canonical, size = inspected
            if canonical in seen:
                break
            seen.add(canonical)
            included = min(size, remaining)
            truncated = truncated or included < size
            if included:
                files.append({"path": canonical, "bytes": included, "totalBytes": size,
                              "estimated": True, "truncated": included < size,
                              "scope": scope, "sectionExposure": "unknown"})
                remaining -= included
            break
    mapping = "not_needed"
    if root is not None and (root / ".git").is_file():
        mapping = "unmapped"
        warnings.append("worktree_mapping_unverified")
    if not cwd.is_dir():
        warnings.append("cwd_unavailable")
    if truncated:
        warnings.append("instruction_byte_limit")
    return {"files": files, "assumptions": list(ASSUMPTIONS), "truncated": truncated,
            "warnings": warnings, "mappingStatus": mapping,
            "sourceStatus": "partial" if warnings else "available"}
