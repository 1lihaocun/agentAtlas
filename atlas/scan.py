#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentAtlas 扫描器
发现本机智能体提示词文件（AGENTS.md / CLAUDE.md / .cursorrules 等），
按 根目录 → 项目 → 子目录 分级，写出到 atlas.json 供前端渲染。

仅用 Python 标准库。重复文件按内容 SHA1 归组（如 codex worktrees 副本）。
"""
import hashlib
import json
import os
import socket
import sys
import time
from pathlib import Path
from collections import defaultdict

HOME = os.path.expanduser("~")

# ============ 平台状态目录（platforms/ 注册表维护） ============
def _platform_scan_roots():
    try:
        from .platforms import scan_root_names
    except ImportError:
        from platforms import scan_root_names
    return scan_root_names()


# ============ 扫描范围：按需增删 ============
ROOTS = [
    "~/Documents/Code",
    "~/Documents/Codex",
    "~/Documents",
    "~/Desktop",
    "~/Downloads",
    "~/clawd",
    "~/OpenWorker",
    "~/test",
    "~/WorkBuddy AI",
    "~/Workbuddy",
]
# 平台目录（.claude/.codex/.cursor/rules/...）来自 platforms/ 注册表
for _name in _platform_scan_roots():
    ROOTS.append("~/" + _name)
# ============ 排除的目录名（任意层级生效）============
PRUNE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "__pycache__",
    "site-packages", "dist", "build", "target", ".next", ".nuxt",
    ".turbo", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    ".npm", ".cargo", ".rustup", ".local", "vendor", "third_party",
    "go", "Library",
}

# 目标文件名（不区分大小写）与 .cursor/rules/ 下的 .mdc
TARGET_NAMES = {
    "agents.md", "claude.md", "gemini.md", "qwen.md",
    ".cursorrules", ".windsurfrules", ".clinerules",
    "copilot-instructions.md",
}
DEFAULT_DEPTH = 9          # 单个根目录内的最大向下层级
MIN_BYTES = 4              # 小于该字节数视为空文件（仍收录，但标记）



def expand(p: str) -> str:
    return os.path.abspath(os.path.expanduser(p))


def walk_root(root: str, depth: int, visited=None):
    """返回该根下命中的文件路径列表。root 可为文件。"""
    try:
        from .catalog import instruction_path_allowed
    except ImportError:
        from catalog import instruction_path_allowed
    def allowed(path):
        return os.path.realpath(path) == path and instruction_path_allowed(path, HOME)
    if visited is None:
        visited = {}
    hits = []
    if not allowed(root):
        return hits
    if os.path.isfile(root):
        if is_target(root):
            hits.append(root)
        return hits
    if visited.get(root, -1) >= depth:
        return hits
    base_depth = root.rstrip(os.sep).count(os.sep)
    for cur, dirs, files in os.walk(root, onerror=lambda e: None):
        remaining = depth - (cur.rstrip(os.sep).count(os.sep) - base_depth)
        # A later, deeper root can have more remaining coverage. Do not prune
        # it merely because an earlier parent reached this directory.
        visited[cur] = max(visited.get(cur, -1), remaining)
        if remaining <= 0:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in PRUNE_DIRS and not d.startswith(".Trash")
                   and visited.get(os.path.join(cur, d), -1) < remaining - 1
                   and allowed(os.path.join(cur, d))]
        for f in files:
            if is_target(f) and allowed(os.path.join(cur, f)):
                hits.append(os.path.join(cur, f))
    return hits


def is_target(name: str) -> bool:
    base = os.path.basename(name).lower()
    return base in TARGET_NAMES or base.endswith(".mdc")


def file_meta(path: str) -> dict:
    try:
        from .catalog import _open_regular
    except ImportError:
        from catalog import _open_regular
    with _open_regular(path) as fh:
        st = os.fstat(fh.fileno())
        raw = fh.read()
    sha = hashlib.sha1(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
        lines = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
    except UnicodeDecodeError:
        text, lines = None, 0
    # 相对某个已知根的展示路径：优先项目名
    rel = os.path.relpath(path, HOME)
    parts = rel.split(os.sep)
    known_user_location = tuple(parts) == (".config", "opencode", "AGENTS.md")
    user_location = (known_user_location or len(parts) == 1
                     or (len(parts) == 2 and parts[0].startswith(".") and parts[0] != ".."))
    if parts and parts[0] == "Documents" and len(parts) > 1 and parts[1] == "Code":
        project = parts[2] if len(parts) > 2 else "(Code 根目录)"
    else:
        project = parts[0]
    return {
        "path": path,
        "name": os.path.basename(path),
        "dir": os.path.dirname(path),
        "root": project,
        # 存放位置候选，不证明加载、继承、覆盖或某次运行实际读取。
        "scope": "user" if user_location else "project",
        "scopeBasis": {"kind": "known_user_location" if known_user_location else "path_location",
                       "observation": "unverified"},
        # worktree 副本标记（git worktree / 任务隔离目录）
        "worktree": any(seg == "worktrees" for seg in parts),
        "bytes": st.st_size,
        "mtime": int(st.st_mtime),
        "lines": lines,
        "sha": sha,
        "empty": st.st_size < MIN_BYTES,
    }


def classify(path: str) -> str:
    n = os.path.basename(path).lower()
    if n.endswith(".mdc"):
        return "cursor-rule"
    if n == "agents.md":
        return "agents"
    if n == "claude.md":
        return "claude"
    if n == "gemini.md":
        return "gemini"
    if n == "qwen.md":
        return "qwen"
    if n == ".cursorrules":
        return "cursor-legacy"
    if n == ".windsurfrules":
        return "windsurf"
    if n == ".clinerules":
        return "cline"
    if n == "copilot-instructions.md":
        return "copilot"
    return "other"


def scan() -> dict:
    t0 = time.time()
    files, errors = [], 0
    unique_counter = defaultdict(list)  # One scan, not process-wide history.
    visited = {}                      # directory -> scanned remaining depth
    seen_paths = set()          # 多根重叠时按真实路径去重
    for r in ROOTS:
        root = expand(r)
        if not os.path.exists(root):
            continue
        for p in walk_root(root, DEFAULT_DEPTH, visited):
            real = os.path.realpath(p)
            if real in seen_paths:
                continue
            seen_paths.add(real)
            try:
                m = file_meta(p)
            except OSError:
                errors += 1
                continue
            m["kind"] = classify(p)
            files.append(m)
            unique_counter[m["sha"]].append(p)

    for m in files:
        m["dup"] = len(unique_counter[m["sha"]]) > 1

    # 根 → 项目 → 子目录 三级聚合
    roots = {}
    for m in files:
        r = roots.setdefault(m["root"], {"name": m["root"], "files": 0, "bytes": 0,
                                         "mtime": 0, "projects": {}})
        r["files"] += 1
        r["bytes"] += m["bytes"]
        r["mtime"] = max(r["mtime"], m["mtime"])
        rel_dir = os.path.dirname(os.path.relpath(m["path"], HOME))
        parts = rel_dir.split(os.sep)
        # Documents/Code/<host>/<repo>/… → 根=host，项目=repo，子目录=其余
        if parts[0] == "Documents" and len(parts) > 1 and parts[1] == "Code":
            proj_parts = parts[3:]
            proj_name = proj_parts[0] if proj_parts else "(Code 根目录)"
        else:
            proj_parts = parts[1:]
            proj_name = proj_parts[0] if proj_parts else "(根目录)"
        pj = r["projects"].setdefault(proj_name, {"name": proj_name, "files": 0,
                                                  "bytes": 0, "mtime": 0, "dirs": {}})
        pj["files"] += 1
        pj["bytes"] += m["bytes"]
        pj["mtime"] = max(pj["mtime"], m["mtime"])
        m["proj"] = proj_name
        if len(proj_parts) > 1:
            sub = "/".join(proj_parts[1:])
            m["sub"] = sub
            dj = pj["dirs"].setdefault(sub, {"path": sub, "files": 0, "bytes": 0, "mtime": 0})
            dj["files"] += 1
            dj["bytes"] += m["bytes"]
            dj["mtime"] = max(dj["mtime"], m["mtime"])
        else:
            m["sub"] = ""

    out_roots = []
    for r in sorted(roots.values(), key=lambda x: -x["files"]):
        r["projects"] = sorted(r["projects"].values(), key=lambda x: -x["files"])
        for pj in r["projects"]:
            pj["dirs"] = sorted(pj["dirs"].values(), key=lambda x: x["path"])
        out_roots.append(r)

    dup_groups = sorted(
        ([p for p in ps] for sha, ps in unique_counter.items() if len(ps) > 1),
        key=len, reverse=True)

    try:
        from .project_identity import project_candidates
    except ImportError:
        from project_identity import project_candidates
    candidates = project_candidates(files, home=HOME)
    candidate_paths = sorted((Path(row['path']) for row in candidates),
                             key=lambda path: len(path.parts), reverse=True)
    for entry in files:
        path = Path(entry['path'])
        entry['projectPath'] = next((str(root) for root in candidate_paths if root in path.parents), None)

    return {
        "generatedAt": int(time.time()),
        "durationMs": int((time.time() - t0) * 1000),
        "host": socket.gethostname(),
        "totalFiles": len(files),
        "totalBytes": sum(m["bytes"] for m in files),
        "uniqueFiles": sum(1 for m in files if not m["dup"]),
        "dupFiles": sum(1 for m in files if m["dup"]),
        "scanErrors": errors,
        "dupGroups": dup_groups,
        "files": sorted(files, key=lambda m: m["path"].lower()),
        "projectCandidates": candidates,
        "roots": out_roots,
    }


def main() -> None:
    try:
        from .asset_settings import atomic_write
    except ImportError:
        from asset_settings import atomic_write
    data = scan()
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "atlas.json")
    atomic_write(out, json.dumps(data, ensure_ascii=False).encode('utf-8'))
    print("写出: %s" % out)
    print("文件 %d（唯一 %d / 重复 %d）  根目录 %d  耗时 %dms" % (
        data["totalFiles"], data["uniqueFiles"], data["dupFiles"],
        len(data["roots"]), data["durationMs"]))
    for r in data["roots"][:12]:
        print("  %-28s %3d 文件 %8d B" % (r["name"], r["files"], r["bytes"]))
    if data["dupGroups"]:
        print("重复组 TOP:")
        for g in data["dupGroups"][:5]:
            print("  ×%d  %s" % (len(g), g[0]))


if __name__ == "__main__":
    sys.exit(main())
