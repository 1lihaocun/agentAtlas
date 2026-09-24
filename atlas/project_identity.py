"""Deterministic project path candidates, not runtime loading assertions.

Display labels (root/proj/sub) never enter identity resolution. Filesystem
inspection is metadata-only; .git contents and source files are never read.
"""
from pathlib import Path
import os
import stat


def _policy():
    # Lazy imports keep the scanner usable both as a module and a script.
    try:
        from . import catalog
    except ImportError:
        import catalog
    return catalog


def safe_path(value, home):
    """Canonical, non-sensitive, non-generated paths within the given boundary."""
    try:
        path = Path(value).expanduser()
        if not path.is_absolute() or '..' in path.parts or '\x00' in str(path):
            return None
        if path.resolve() != path or _policy().is_sensitive_path(path):
            return None
        relative = path.relative_to(home) if path.is_relative_to(home) else path
        if _policy()._pruned(relative, 'shared'):
            return None
        return path
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _user_owned(path, home):
    roots = [home / name for name, _ in _policy()._USER_ROOTS()]
    if path == home or path in home.parents or any(path == root or root in path.parents for root in roots):
        return True
    return any(parent.name.startswith('.openclaw') and parent.parent == home
               for parent in (path, *path.parents))


def _anchor(path, home):
    containers = {name.split('/')[0] for name, _ in _policy()._PROJECT_ROOTS()}
    for parent in path.parents:
        if parent == home:
            break
        if parent.name in containers or parent.name == '.github':
            return parent.parent
    return path.parent


def project_candidates(instruction_files=(), project_roots=(), home=None,
                       scan_roots=(), max_depth=9, prune_dirs=()):
    """Return stable candidates, preserving explicit and .git-marked boundaries.

    With no repository evidence, instruction directories are path candidates.
    A nested platform workspace is not swallowed by a weak ancestor instruction;
    inside a marked/explicit repository, its scoped assets retain that owner.
    Explicit roots remain independent, including nested roots.
    """
    home = Path(home or Path.home()).expanduser().resolve()
    evidence = {}
    strong = set()
    containers = {name.split('/')[0] for name, _ in _policy()._PROJECT_ROOTS()}

    def add(path, kind, source, independent=False, scan_root='', depth=-1):
        if path is None or _user_owned(path, home):
            return
        evidence.setdefault(path, set()).add((kind, str(source), str(scan_root), depth))
        if independent:
            strong.add(path)

    def git_parent(anchor):
        for parent in (anchor, *anchor.parents):
            if parent == home or parent == parent.parent:
                break
            if safe_path(parent, home) is None:
                break
            # Platform directories are workspace path evidence even without Git.
            # Ancestor instruction files alone do not make a repository boundary.
            for name in sorted(containers):
                location = safe_path(parent / name, home)
                if location is not None and location.is_dir():
                    add(parent, 'asset_directory', location)
            marker = parent / '.git'
            try:
                mode = marker.lstat().st_mode
                if stat.S_ISDIR(mode) or stat.S_ISREG(mode):
                    add(parent, 'git_marker', marker, True)
                    break
            except OSError:
                pass

    for value in project_roots or ():
        path = safe_path(value, home)
        add(path, 'explicit', path, True)
    for entry in instruction_files or ():
        raw = entry.get('path') if isinstance(entry, dict) else None
        # Keep same-directory instruction aliases as restricted inventory
        # metadata, but never traverse a linked directory to infer identity.
        try:
            path = Path(raw)
        except (TypeError, ValueError):
            continue
        if not path.is_absolute() or '..' in path.parts or _policy().is_sensitive_path(path):
            continue
        try:
            resolved = path.resolve()
            if resolved != path and (resolved.parent != path.parent or not _policy()._instruction(path)):
                continue
        except (OSError, RuntimeError, ValueError):
            continue
        if safe_path(path if resolved == path else path.parent, home) is None:
            continue
        anchor = safe_path(_anchor(path, home), home)
        if anchor is None or _user_owned(anchor, home):
            continue
        add(anchor, 'instruction_directory', path)
        git_parent(anchor)

    # Only metadata-walk explicitly authorized ranges; never sweep HOME or
    # platform state/session stores looking for repositories.
    for value in scan_roots or ():
        value = str(value)
        root = safe_path(home / value[2:] if value.startswith('~/') else value, home)
        if (root is None or root == home or root in home.parents
                or _user_owned(root, home) or not root.is_dir()):
            continue
        container = None
        for parent in (root, *root.parents):
            if parent == home or parent == parent.parent:
                break
            if parent.name in containers:
                container = parent
                break
        if container is not None:
            add(container.parent, 'asset_directory', container,
                scan_root=root, depth=max_depth)
            git_parent(container.parent)
            continue
        for base, dirs, _files in os.walk(root, followlinks=False):
            base = Path(base)
            level = len(base.relative_to(root).parts)
            safe_dirs = [name for name in dirs if name not in prune_dirs
                         and not name.startswith('.Trash')
                         and safe_path(base / name, home) is not None]
            if level < max_depth:
                for name in safe_dirs:
                    if name in containers:
                        add(base, 'asset_directory', base / name,
                            scan_root=root, depth=max_depth)
                        git_parent(base)
            dirs[:] = [name for name in safe_dirs if name not in containers] if level < max_depth else []

    retained = []
    for path in sorted(evidence, key=lambda p: (len(p.parts), str(p))):
        workspace = any(source[0] == 'asset_directory' for source in evidence[path])
        independent_workspace = workspace and not any(root in path.parents for root in strong)
        if path in strong or independent_workspace or not any(root in path.parents for root in retained):
            retained.append(path)
        else:
            owner = max((root for root in retained if root in path.parents), key=lambda p: len(p.parts))
            evidence[owner].update(evidence[path])
    return [{'path': str(path), 'status': 'candidate',
             'sources': [dict(kind=kind, path=source,
                              **({'scanRoot': scan_root, 'maxDepth': depth} if scan_root else {}))
                         for kind, source, scan_root, depth in sorted(evidence[path])],
             'observation': 'unverified'}
            for path in sorted(retained, key=str)]
