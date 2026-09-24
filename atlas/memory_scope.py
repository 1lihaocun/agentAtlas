"""Conservative, read-only memory semantics (rules checked 2026-09-22).

Only supplied catalog metadata and optional reviewed instruction evidence are
inspected. No files, configuration, credentials, transcripts, environment
overrides, or runtime telemetry are read; resource/note contents stay opaque.
See docs/memory-rules.md for provenance and deliberate unsupported cases.

已核对的平台记忆读取规则放在 ``platforms/<id>/``（resolve_memory），
本模块负责分发与聚合，不再持有任何平台细节。
"""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import re

try:
    from .platforms import semantics as _shared
    from .platforms import memory_resolver
except ImportError:  # Also support imports beside scan.py when run as a script.
    from platforms import semantics as _shared
    from platforms import memory_resolver

semantics = _shared.semantics
_LEVELS = _shared._LEVELS


def _absolute(value):
    """Lexical validation only: never stat/resolve/open supplied paths."""
    if not isinstance(value, (str, Path)) or not str(value) or "\x00" in str(value):
        return None
    path = Path(value)
    return path if path.is_absolute() and ".." not in path.parts else None


def _relative(path, root):
    if path is None or root is None:
        return ()
    try:
        return path.relative_to(root).parts
    except ValueError:
        return ()


def _resolve(asset, roots, home, extension_evidence):
    ctx = _shared.context(asset, roots, home)
    if ctx is None:
        return semantics()
    resolver = memory_resolver(asset.get("platform", ""))
    if resolver is None:
        return semantics()
    ctx["extension_evidence"] = extension_evidence
    return resolver(ctx)


def _project_paths(files, supplied, home):
    candidates = []
    if supplied is not None:
        candidates = list(supplied)
    else:
        for asset in files:
            path, root = _absolute(asset.get("path")), _absolute(asset.get("project"))
            relative = _relative(root, home)
            if (path is not None and root is not None and root in path.parents
                    and not (relative and relative[0].startswith("."))):
                candidates.append(root)
    return sorted({str(path) for value in candidates for path in [_absolute(value)]
                   if path is not None and path != home and path != Path(path.anchor)})


def build_memory_view(files, project_roots=None, home=None, project='', platform='', profile='',
                      extension_evidence=None):
    """Return a schema-v1 memory view without mutating the supplied catalog.

    ``project_roots`` are explicit known project directories. When omitted, only
    absolute catalog ``project`` paths containing their asset can supply options.
    Options are not proof that arbitrary memory under a project is loaded.
    Counts.total is the unfiltered distinct memory inventory; levels count the
    matched rows. Empty selectors mean all. Filters never add new project roots.
    ``extension_evidence`` optionally maps exact absolute instructions.md paths
    to {content: str, sha256: str, truncated: bool}, supplied by a trusted bounded
    reader. Only complete, hash-matching reviewed declarations are accepted.
    This function performs no I/O or execution of declarations. Local evidence
    establishes an input role, never actual consumption or project applicability.
    """
    files = list(files)
    home = _absolute(home if home is not None else Path.home())
    roots = _project_paths(files, project_roots, home)
    memories = []
    seen = {}
    for original in files:
        if original.get("category") != "memory":
            continue
        key = original.get("path")
        if key in seen:
            prior = seen[key]
            if any(prior.get(field, default) != original.get(field, default)
                   for field, default in (("platform", ""), ("profile", "default"))):
                prior["semantics"] = semantics(detail="同一路径的清单平台或配置档元数据冲突，不推断生效范围")
            continue
        asset = deepcopy(original)
        asset["semantics"] = _resolve(asset, roots, home, extension_evidence)
        seen[key] = asset
        memories.append(asset)
    matched = [asset for asset in memories
               if (not platform or asset.get("platform") == platform)
               and (not profile or asset.get("profile", "default") == profile)
               and (not project or project in asset["semantics"]["appliesTo"])]
    return {"schemaVersion": 1, "files": matched,
            "projects": [{"path": path, "name": Path(path).name} for path in roots],
            "platforms": sorted({asset.get("platform", "") for asset in memories} - {""}),
            "profiles": sorted({asset.get("profile", "default") for asset in memories}),
            "counts": {"total": len(memories), "matched": len(matched),
                       "levels": dict(sorted(Counter(a["semantics"]["level"] for a in matched).items()))},
            "selected": {"project": project, "platform": platform, "profile": profile},
            "notice": "存储位置不等于生效范围；规则与路径推断，不是运行时追踪；未验证实际读取。"}
