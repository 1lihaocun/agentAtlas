#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentAtlas 本地服务
- GET  /                  → web/index.html
- GET  /api/tree          → atlas.json（瘦身：只给树和文件列表元数据）
- GET  /api/file?path=…   → 读取文件原文
- GET  /api/sections?path=… → 按 markdown 标题解析文件的 section 组件
- GET  /api/usage         → 指令文件曝光统计（会话日志 × 生效栈 join）
- POST /api/save          → 版本校验后保存（备份到 .agentatlas/lifecycle/backups）
- POST /api/rescan        → 重新执行扫描器
- POST /api/section/save  → 保存单个 section（整文件重组写回，备份先行）

仅标准库，默认 127.0.0.1:7788。
"""
from contextlib import contextmanager
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from .asset_service import AssetService
    from .asset_files import FileProblem
    from .sections import snapshot, replace_section, parse_sections, Conflict as SectionConflict, InvalidUTF8
    from .storage import Store, StorageProblem
    from .lifecycle import ReviewService
    from .provenance import ProvenanceService
except ImportError:
    from asset_service import AssetService
    from asset_files import FileProblem
    from sections import snapshot, replace_section, parse_sections, Conflict as SectionConflict, InvalidUTF8
    from storage import Store, StorageProblem
    from lifecycle import ReviewService
    from provenance import ProvenanceService

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
WEB = os.path.join(PROJECT, "web")
ATLAS_JSON = os.path.join(PROJECT, "atlas.json")
BACKUP_DIR = os.path.expanduser("~/.hermes/cache/scratch/agent-atlas-backups")
HOME = os.path.expanduser("~")

ALLOWED_SUFFIX = {".md", ".mdc", ".rules", ".txt"}
ALLOWED_BASENAMES = {".cursorrules", ".cursorindexingignore", ".windsurfrules", ".clinerules"}
HOST, PORT = "127.0.0.1", 7788

# In-process admission only; AssetService still owns its worker/save locks.
_INDEX_REQUEST_LOCK = threading.Lock()

# 生效提示词：各工具的用户级全局指令
EFFECTIVE_GLOBAL = [
    ("Claude", ".claude/CLAUDE.md"),
    ("Codex/通用", ".codex/AGENTS.md"),
    ("Gemini", ".gemini/GEMINI.md"),
    ("Qwen", ".qwen/QWEN.md"),
]
# 生效提示词：每个目录层级上会命中的文件名
LEVEL_FILES = ["AGENTS.md", "CLAUDE.md", "GEMINI.md", "QWEN.md",
               ".cursorrules", ".windsurfrules", ".clinerules"]


def file_info(path):
    """读取文件的完整信息（含内容），用于生效提示词栈。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", "replace")
    import hashlib
    return {
        "path": path,
        "name": os.path.basename(path),
        "bytes": len(raw),
        "lines": text.count("\n") + (0 if not text or text.endswith("\n") else 1),
        "sha1": hashlib.sha1(raw).hexdigest(),
        "empty": len(raw) < 4,
        "content": text,
    }


def load_index():
    if not os.path.exists(ATLAS_JSON):
        return None
    with open(ATLAS_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


def bounded_int(qs, name, default, low, high):
    values = qs.get(name, [str(default)])
    if len(values) != 1:
        raise ValueError("重复的参数：" + name)
    value = int(values[0])
    if not low <= value <= high:
        raise ValueError("参数超出范围：" + name)
    return value


# ---------- 曝光统计：会话日志 × 生效栈 ----------
def _collect_sessions(days):
    """从各工具的会话痕迹收集每一次会话的 (cwd, 时间戳, 工具名)。

    单位是「会话文件」而不是「目录」：一个项目里可以有几十次会话，
    每次会话都注入了一次指令文件。
    数据源（只读，绝不改动）：
    - Hermes 会话 ~/.hermes/sessions/**/*.json(l)，头部 cwd 字段
    - Codex 会话 ~/.codex/sessions/**/*.jsonl，头部 cwd 字段
    - Claude 项目 ~/.claude/projects/<编码路径>/*.jsonl，每个文件一次会话
    """
    cutoff = time.time() - days * 86400
    sessions = []    # [(dir, ts, tool), ...] 每个会话文件一条

    def _push(d, ts, tool):
        if ts < cutoff:
            return
        try:
            rp = os.path.realpath(d)
        except Exception:
            return
        if rp.startswith(HOME) and os.path.isdir(rp):
            sessions.append((rp, ts, tool))

    def _cwd_from_head(fp, head_bytes=65536, head_lines=40):
        """从会话文件头部找 cwd 字段。"""
        try:
            with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                if fp.endswith(".jsonl"):
                    for _ in range(head_lines):
                        ln = fh.readline()
                        if not ln:
                            break
                        m = re.search(r'"cwd"\s*:\s*"([^"]+)"', ln)
                        if m:
                            return m.group(1).replace("\\\\", "\\").replace('\\"', '"')
                else:
                    head = fh.read(head_bytes)
                    m = re.search(r'"cwd"\s*:\s*"([^"]+)"', head)
                    if m:
                        return m.group(1).replace("\\\\", "\\").replace('\\"', '"')
        except Exception:
            pass
        return None

    # Hermes：每个会话文件一次会话（desktop 会话 cwd 为空，只贡献全局）
    hs = os.path.join(HOME, ".hermes", "sessions")
    if os.path.isdir(hs):
        for root, _dirs, files in os.walk(hs):
            for fn in files:
                if not fn.endswith((".json", ".jsonl")):
                    continue
                fp = os.path.join(root, fn)
                try:
                    mt = os.path.getmtime(fp)
                except OSError:
                    continue
                if mt < cutoff:
                    continue
                cwd = _cwd_from_head(fp)
                if cwd:
                    _push(cwd, mt, "Hermes")
    # Codex：每个 rollout jsonl 一次会话
    cs = os.path.join(HOME, ".codex", "sessions")
    if os.path.isdir(cs):
        for root, _dirs, files in os.walk(cs):
            for fn in files:
                if not fn.endswith(".jsonl"):
                    continue
                fp = os.path.join(root, fn)
                try:
                    mt = os.path.getmtime(fp)
                except OSError:
                    continue
                if mt < cutoff:
                    continue
                cwd = _cwd_from_head(fp)
                if cwd:
                    _push(cwd, mt, "Codex")
    # Claude Code：项目目录下每个会话 jsonl 一次会话
    cp = os.path.join(HOME, ".claude", "projects")
    if os.path.isdir(cp):
        for name in os.listdir(cp):
            pd = os.path.join(cp, name)
            if not os.path.isdir(pd):
                continue
            guess = name.replace("-", "/")
            if not os.path.isdir(os.path.realpath(guess)):
                continue
            try:
                for fn in os.listdir(pd):
                    if not fn.endswith((".jsonl", ".json")):
                        continue
                    fp = os.path.join(pd, fn)
                    try:
                        mt = os.path.getmtime(fp)
                    except OSError:
                        continue
                    if mt < cutoff:
                        continue
                    cwd = _cwd_from_head(fp) or guess
                    _push(cwd, mt, "Claude Code")
            except OSError:
                continue
    return sessions


def _count_reads(days, cutoff):
    """全文扫描会话日志，统计索引内文件被智能体「主动读取」的会话数。

    信号：会话内容中出现文件的绝对路径（read/shell 调用参数里的真实路径，
    相对路径字样如 `backend/AGENTS.md` 大多是规则转述，不算）。
    实现：单趟正则提取所有 HOME 开头的指令文件路径 token，再查索引集合
    （196 次 `in` 全文扫太慢，正则一遍 6 倍快）。返回 {path: sessions_with_read}。
    """
    idx = load_index()
    if idx is None:
        return {}
    indexed = {f["path"] for f in idx["files"]}
    reads = {p: 0 for p in indexed}
    esc = re.escape(HOME)
    path_re = re.compile(esc + r"[^\s\"'`\\)\]}]*?(?:\.(?:md|mdc|rules|txt))")
    log_dirs = [
        (os.path.join(HOME, ".codex", "sessions"), ".jsonl"),
        (os.path.join(HOME, ".claude", "projects"), ".jsonl"),
        (os.path.join(HOME, ".claude", "projects"), ".json"),
        (os.path.join(HOME, ".hermes", "sessions"), ".json"),
        (os.path.join(HOME, ".hermes", "sessions"), ".jsonl"),
    ]
    seen_bases = set(log_dirs)
    for base, suffix in log_dirs:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.endswith(suffix):
                    continue
                fp = os.path.join(root, fn)
                try:
                    if os.path.getmtime(fp) < cutoff:
                        continue
                    with open(fp, "r", encoding="utf-8", errors="replace") as fh:
                        body = fh.read()
                except Exception:
                    continue
                found = set()
                for mm in path_re.finditer(body):
                    tok = mm.group(0)
                    if tok in indexed:
                        found.add(tok)
                    else:
                        # 贪婪匹配可能吞掉尾部：逐级回退目录前缀再试探
                        parts = tok.rsplit("/", 1)
                        while len(parts) == 2 and parts[0].count("/") > 2:
                            prefix = parts[0]
                            if prefix in indexed:
                                found.add(prefix)
                                break
                            parts = prefix.rsplit("/", 1)
                for p in found:
                    reads[p] += 1
    return reads


def _effective_files_for_dir(dirp):
    """某目录发起会话时会加载的指令文件集合（用户级 + 祖先链）。"""
    files = set()
    for _label, rel in EFFECTIVE_GLOBAL:
        p = os.path.join(HOME, *rel.split("/"))
        if os.path.isfile(p):
            files.add(os.path.realpath(p))
    cur = dirp
    while True:
        for name in LEVEL_FILES:
            p = os.path.join(cur, name)
            if os.path.isfile(p):
                files.add(os.path.realpath(p))
        crd = os.path.join(cur, ".cursor", "rules")
        if os.path.isdir(crd):
            try:
                for f in os.listdir(crd):
                    if f.lower().endswith(".mdc"):
                        files.add(os.path.realpath(os.path.join(crd, f)))
            except OSError:
                pass
        parent = os.path.dirname(cur)
        if parent == cur or cur == HOME:
            break
        cur = parent
    return files


def _map_worktree_to_main(dirp):
    """~/.codex/worktrees/<id>/<repo>/… 是 git worktree 隔离副本，
    真实项目在 ~/Documents/Code/<host>/<repo>。找到则归并到主仓库路径。"""
    marker = os.sep + "worktrees" + os.sep
    if marker not in dirp:
        return dirp
    try:
        idx = dirp.index(marker) + len(marker)
        rest = dirp[idx:]                      # <id>/<repo>[/sub…]
        repo = rest.split(os.sep, 1)[1] if os.sep in rest else None
        if not repo:
            return dirp
        home_code = os.path.join(HOME, "Documents", "Code")
        if os.path.isdir(home_code):
            for host in sorted(os.listdir(home_code)):
                cand = os.path.join(home_code, host, repo)
                if os.path.isdir(cand):
                    return cand
    except Exception:
        pass
    return dirp


def compute_usage(days=30, use_cache=True):
    """曝光统计：每个索引内文件「近 N 天在多少次会话中被加载」。
    - 单位是会话（每次会话注入一次指令），不是目录
    - worktree 会话归并到主仓库（同一份项目工作的隔离副本）
    - 按工具分列（Codex / Claude Code / Hermes）
    - 附带「主动读取」计数：会话内容出现文件绝对路径的会话数
    - 磁盘缓存 10 分钟 TTL（全文扫日志约 15-20 秒，不能每次请求都算）"""
    cache_path = os.path.join(os.path.dirname(ATLAS_JSON), ".usage-cache.json")
    if use_cache:
        try:
            if os.path.isfile(cache_path) and time.time() - os.path.getmtime(cache_path) < 600:
                with open(cache_path, "r", encoding="utf-8") as fh:
                    cached = json.load(fh)
                if cached.get("days") == days:
                    return cached
        except Exception:
            pass
    idx = load_index()
    if idx is None:
        return None
    cutoff = time.time() - days * 86400
    raw_sessions = _collect_sessions(days)
    # worktree 归并：~/.codex/worktrees/<id>/<repo>/… → Documents/Code/<host>/<repo>
    sessions = []
    for d, ts, tool in raw_sessions:
        sessions.append((_map_worktree_to_main(d), ts, tool))
    wt_merged = len(sessions) - len(set(d for d, _t, _ in sessions))
    per_file = {f["path"]: {"sessions": 0, "last": 0, "byTool": {}} for f in idx["files"]}
    tool_totals = {}
    for d, ts, tool in sessions:
        tool_totals[tool] = tool_totals.get(tool, 0) + 1
        for p in _effective_files_for_dir(d):
            if p in per_file:
                per_file[p]["sessions"] += 1
                per_file[p]["last"] = max(per_file[p]["last"], ts)
                per_file[p]["byTool"][tool] = per_file[p]["byTool"].get(tool, 0) + 1
    reads = _count_reads(days, cutoff)
    now = time.time()
    rows = []
    for f in idx["files"]:
        u = per_file[f["path"]]
        rows.append({"path": f["path"], "sessions": u["sessions"],
                     "byTool": u["byTool"], "reads": reads.get(f["path"], 0),
                     "lastEffective": u["last"] or None,
                     "daysSinceEdit": max(0, int((now - f.get("mtime", 0)) / 86400)),
                     "bytes": f["bytes"], "dup": f.get("dup", False),
                     "scope": f.get("scope", ""), "worktree": f.get("worktree", False)})
    rows.sort(key=lambda r: -(r["sessions"] + r["reads"]))
    result = {"days": days, "sessionsScanned": len(sessions), "worktreesMerged": wt_merged,
              "byTool": tool_totals, "generatedAt": now, "files": rows,
              "cachedAt": now}
    try:
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False)
    except Exception:
        pass
    return result


def scan_file_sessions(target, days=30):
    """某指令文件在会话日志中的出现明细：哪些会话、每处出现的行号/类型/时间。

    target 必须在索引内（防路径探测）。返回按出现次数排序的会话列表，
    每条含 sessionId（可 codex resume）与逐条命中（行号、动作类型、时间）。
    """
    idx = load_index()
    indexed = {f["path"] for f in idx["files"]} if idx else set()
    if target not in indexed:
        return None
    cutoff = time.time() - days * 86400
    bases = [(os.path.join(HOME, ".codex", "sessions"), ".jsonl", "Codex"),
             (os.path.join(HOME, ".claude", "projects"), ".jsonl", "Claude Code"),
             (os.path.join(HOME, ".hermes", "sessions"), ".json", "Hermes"),
             (os.path.join(HOME, ".hermes", "sessions"), ".jsonl", "Hermes")]
    out = []
    for base, suffix, tool in bases:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.endswith(suffix):
                    continue
                fp = os.path.join(root, fn)
                try:
                    if os.path.getmtime(fp) < cutoff:
                        continue
                    lines = open(fp, encoding="utf-8", errors="replace").readlines()
                except Exception:
                    continue
                hits = []
                for i, ln in enumerate(lines, 1):
                    cnt = ln.count(target)
                    if not cnt:
                        continue
                    kind, ts = "", ""
                    try:
                        obj = json.loads(ln)
                        ts = (obj.get("timestamp") or "")[:16].replace("T", " ")
                        p = obj.get("payload", {}) or {}
                        pt = p.get("type", "")
                        if pt in ("custom_tool_call", "function_call"):
                            kind = "工具调用 " + (p.get("name") or "")
                        elif pt in ("custom_tool_call_output", "function_call_output"):
                            kind = "工具输出"
                        elif pt == "compacted":
                            kind = "上下文压缩"
                        elif pt == "message":
                            kind = "消息(" + (p.get("role") or "?") + ")"
                        else:
                            kind = pt or obj.get("type", "")
                    except Exception:
                        kind = "?"
                    hits.append({"line": i, "count": cnt, "kind": kind, "ts": ts})
                if not hits:
                    continue
                sid, start = "", ""
                if lines:
                    try:
                        meta = json.loads(lines[0])
                        mp = meta.get("payload") or {}
                        sid = mp.get("id") or mp.get("session_id") or ""
                        start = (meta.get("timestamp") or "")[:16].replace("T", " ")
                    except Exception:
                        pass
                out.append({"file": fp, "tool": tool, "sessionId": sid, "start": start,
                            "hitsTotal": sum(h["count"] for h in hits),
                            "callCount": sum(1 for h in hits if h["kind"].startswith("工具调用")),
                            "hits": hits[:12]})
    out.sort(key=lambda x: -x["hitsTotal"])
    return {"path": target, "days": days, "sessions": out,
            "totalAppear": sum(s["hitsTotal"] for s in out)}


def slim_index(data):
    """前端首屏只需要树 + 文件元数据，不带正文。"""
    return {k: v for k, v in data.items() if k != "files"} | {
        "files": [{k: v for k, v in f.items() if k != "content"} for f in data["files"]],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ---------- helpers ----------
    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _static(self, rel):
        full = os.path.normpath(os.path.join(WEB, rel))
        if os.path.commonpath((os.path.realpath(full), WEB)) != WEB or not os.path.isfile(full):
            self._json({"ok": False, "error": "not found"}, 404)
            return
        ctype = {"html": "text/html; charset=utf-8",
                 "js": "text/javascript; charset=utf-8",
                 "css": "text/css; charset=utf-8"}.get(full.rsplit(".", 1)[-1],
                                                       "application/octet-stream")
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ---------- GET ----------
    def do_GET(self):
        if not self._trusted_request():
            return
        try:
            self._get()
        except (FileProblem, StorageProblem, SectionConflict, InvalidUTF8) as exc:
            self._json({"ok": False, "error": str(exc), "code": getattr(exc, "code", "file_error")}, exc.status)
        except ValueError as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self._json({"ok": False, "error": "请求失败（%s）" % type(exc).__name__}, 500)

    def _trusted_request(self):
        """Block DNS rebinding and cross-origin access to the local file service."""
        try:
            host = urllib.parse.urlsplit("http://" + self.headers.get("Host", ""))
            if host.hostname not in ("localhost", "127.0.0.1", "::1") or host.port != self.server.server_port:
                raise ValueError()
            origin = self.headers.get("Origin")
            if origin:
                parsed = urllib.parse.urlsplit(origin)
                if parsed.scheme != "http" or parsed.netloc != host.netloc:
                    raise ValueError()
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise ValueError()
        except ValueError:
            self.close_connection = True
            self._json({"ok": False, "error": "仅允许同源的本机请求"}, 403)
            return False
        return True

    def _get(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path in ("/", "/index.html"):
            self._static("index.html")
        elif parsed.path == "/lifecycle.js":
            self._static("lifecycle.js")
        elif parsed.path == "/api/retirement":
            if qs.get("tool", ["Codex"]) != ["Codex"]:
                raise ValueError("目前仅支持 Codex 观测范围")
            result = self.server.lifecycle.queue(bounded_int(qs, "days", 30, 1, 365),
                                                 bounded_int(qs, "staleDays", 90, 1, 3650))
            self._json({"ok": True, "data": result})
        elif parsed.path == "/api/usage/evidence":
            result = self.server.lifecycle.evidence((qs.get("path") or [""])[0],
                                                    bounded_int(qs, "days", 30, 1, 365))
            self._json({"ok": True, "data": result})
        elif parsed.path == "/api/assets":
            self._json({"ok": True, "data": self.server.assets.snapshot()})
        elif parsed.path == "/api/file-guide":
            if set(qs) - {'path'} or any(len(values) != 1 or not values[0] or '\x00' in values[0] or len(values[0]) > 4096 for values in qs.values()):
                raise ValueError('文件说明参数无效或重复')
            self._json({'ok': True, 'data': self.server.assets.file_guides(**{key: values[0] for key, values in qs.items()})})
        elif parsed.path == "/api/provenance":
            if set(qs) - {'path'} or any(len(values) != 1 or not values[0] or '\x00' in values[0] or len(values[0]) > 4096 for values in qs.values()):
                raise ValueError('生成机制参数无效或重复')
            self._json({'ok': True, 'data': self.server.provenance((qs.get('path') or [''])[0])})
        elif parsed.path == "/api/memories":
            allowed = {"project", "platform", "profile"}
            if set(qs) - allowed or any(len(values) != 1 or "\x00" in values[0] or
                                        len(values[0]) > 4096 for values in qs.values()):
                raise ValueError("记忆筛选参数无效或重复")
            result = self.server.assets.memories(**{key: values[0] for key, values in qs.items()})
            self._json({"ok": True, "data": result})
        elif parsed.path == "/api/assets/status":
            self._json({"ok": True, "data": self.server.assets.status()})
        elif parsed.path == "/api/settings":
            self._json({"ok": True, "data": self.server.assets.settings.public()})
        elif parsed.path == "/api/search":
            mode = (qs.get("mode") or ["keyword"])[0]
            if mode not in ("keyword", "vector", "hybrid"):
                raise FileProblem("不支持的检索模式")
            filters = {key: qs[key][0] for key in ("platform", "category", "project") if qs.get(key) and qs[key][0]}
            result = self.server.assets.search((qs.get("q") or [""])[0], mode=mode,
                                              filters=filters, limit=max(1, min(200, int((qs.get("limit") or ["50"])[0]))))
            self._json({"ok": True, "data": result})
        elif parsed.path == "/api/tree":
            data = load_index()
            if data is None:
                self._json({"ok": False, "error": "尚未扫描：请先运行 atlas/scan.py 或点「重新扫描」"}, 404)
            else:
                self._json({"ok": True, "data": slim_index(data)})
        elif parsed.path == "/api/file":
            qs = urllib.parse.parse_qs(parsed.query)
            path = (qs.get("path") or [""])[0]
            self._serve_file(path)
        elif parsed.path == "/api/effective":
            self._effective(qs)
        elif parsed.path == "/api/sections":
            self._sections(urllib.parse.parse_qs(parsed.query))
        elif parsed.path == "/api/usage":
            self._usage(qs)
        elif parsed.path == "/api/usage/sessions":
            self._usage_sessions(qs)
        else:
            self._json({"ok": False, "error": "unknown endpoint"}, 404)

    def _sections(self, qs):
        """按 markdown 标题解析文件为 section 组件（GEPA 式进化单元）。"""
        path = (qs.get("path") or [""])[0]
        data = self.server.assets.file_store.read(path)
        suffix = os.path.splitext(path)[1].lower()
        if suffix not in (".md", ".markdown"):
            self._json({"ok": False, "error": "仅支持 markdown 文件的 section 解析"}, 400)
            return
        text = data["content"]
        if not data["sha256"]:
            raise FileProblem("截断或编码不完整的文件不支持组件编辑", 422)
        doc = snapshot(text.encode("utf-8"))
        sections = doc["sections"]
        self._json({"ok": True, "data": {
            "path": path, "sections": sections,
            "totalChars": len(text), "sectionCount": len(sections),
            "sha256": data["sha256"], "version": doc["version"], "editable": data["editable"]}})

    def _usage(self, qs):
        """Codex retained-log estimates; unknown and mentions are explicit."""
        days = bounded_int(qs, "days", 30, 1, 365)
        data = self.server.lifecycle.usage(days)
        self._json({"ok": True, "data": data})

    def _usage_sessions(self, qs):
        """Legacy route alias: metadata-only evidence, never path matches as reads."""
        data = self.server.lifecycle.evidence((qs.get("path") or [""])[0],
                                              bounded_int(qs, "days", 30, 1, 365))
        self._json({"ok": True, "data": data})

    def _effective(self, qs):
        """Same current-layout Codex estimate as usage, without instruction contents."""
        if qs.get("tool", ["Codex"]) != ["Codex"] or len(qs.get("dir", [])) != 1:
            raise ValueError("请指定一个目录；目前仅支持 Codex 推算")
        value = qs["dir"][0]
        if not value or not os.path.isabs(value) or "\x00" in value:
            raise ValueError("目录路径无效")
        dirp = os.path.realpath(value)
        if not dirp or not os.path.isdir(dirp):
            self._json({"ok": False, "error": "目录不存在"}, 404)
            return
        if not (dirp == HOME or dirp.startswith(HOME + os.sep)):
            self._json({"ok": False, "error": "仅支持 HOME 内的目录"}, 403)
            return
        result = self.server.lifecycle.effective(dirp)
        self._json({"ok": True, "data": dict(result, dir=dirp, tool="Codex")})

    def _serve_file(self, path):
        data = self.server.assets.file_store.read(path)
        memories = getattr(self.server.assets, "memories", None)
        if data.get("category") == "memory" and memories is not None:
            entry = next((f for f in memories()["files"] if f["path"] == data["path"]), None)
            if entry is not None:
                data["semantics"] = entry["semantics"]
                if 'memoryInfo' in entry:
                    data['memoryInfo'] = entry['memoryInfo']
                if 'memoryStorage' in entry:
                    data['memoryStorage'] = entry['memoryStorage']
        describe = getattr(self.server.assets, 'file_guides', None)
        if describe is not None:
            try:
                data['fileGuide'] = describe(data['path'])
            except FileProblem as exc:
                if exc.status != 503:
                    raise
                # A documentation edit must not break safe source-file preview.
                data['fileGuideError'] = str(exc)
        self._json({"ok": True, "data": dict(data, version=data["sha256"])})

    # ---------- POST ----------
    def do_POST(self):
        if not self._trusted_request():
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > 4 * 1024 * 1024:
                self.close_connection = True
                raise FileProblem("请求体过大", 413)
            if self.headers.get_content_type() != "application/json":
                self.close_connection = True
                raise FileProblem("仅接受 JSON 请求", 415)
            req = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
            if not isinstance(req, dict):
                raise FileProblem("请求必须是 JSON 对象")
            self._post(req)
        except (FileProblem, StorageProblem, SectionConflict, InvalidUTF8) as exc:
            self._json({"ok": False, "error": str(exc), "code": getattr(exc, "code", "file_error"),
                        "changed": getattr(exc, "changed", False)}, exc.status)
        except (ValueError, UnicodeError) as exc:
            self._json({"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            self._json({"ok": False, "error": "请求失败（%s）" % type(exc).__name__}, 500)

    def _post(self, req):
        if self.path == "/api/save":
            self._save(req)
        elif self.path == "/api/retirement/decision":
            result = self.server.lifecycle.decide(req.get("path", ""), req.get("baseVersion"),
                                                  req.get("action"), req.get("snoozeDays"))
            self._json({"ok": True, "data": result})
        elif self.path == "/api/settings":
            self._json({"ok": True, "data": self.server.assets.settings.save(req)})
        elif self.path == "/api/search/index":
            with self._index_request():
                job = self.server.assets.start(embeddings=req.get("embeddings") is True,
                                               confirm_cloud=req.get("confirmCloud") is True,
                                               max_chunks=req.get("maxChunks", 256))
            self._json({"ok": True, "data": job}, 202)
        elif self.path in ("/api/rescan", "/api/assets/rescan"):
            if req:
                raise FileProblem("重新扫描不接受参数；云端向量化请使用独立索引入口")
            self._rescan()
        elif self.path == "/api/open":
            self._open(req)
        elif self.path == "/api/sync-dups":
            self._sync_dups(req)
        elif self.path == "/api/translate":
            self._translate(req)
        elif self.path == "/api/section/save":
            self._section_save(req)
        else:
            self._json({"ok": False, "error": "unknown endpoint"}, 404)

    def _save(self, req):
        base = req.get("baseVersion", req.get("baseSha256"))
        if not base:
            self.server.assets.file_store.entry(req.get("path", ""), write=True)
            raise FileProblem("缺少文件版本，请重新打开文件后保存", 428)
        result = self.server.assets.file_store.save(req.get("path", ""), req.get("content", ""), base)
        if result["changed"]:
            self.server.assets.refresh()
        self._json({"ok": True, "data": result})

    @contextmanager
    def _index_request(self):
        if not _INDEX_REQUEST_LOCK.acquire(blocking=False):
            raise FileProblem("已有扫描或索引请求运行中，请稍后重试", 409)
        try:
            if self.server.assets.status()["job"]["running"]:
                raise FileProblem("已有索引任务运行中，请稍后重试", 409)
            yield
        finally:
            _INDEX_REQUEST_LOCK.release()

    def _rescan(self):
        with self._index_request():
            r = subprocess.run([sys.executable, os.path.join(HERE, "scan.py")],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                self._json({"ok": False, "error": (r.stderr or r.stdout)[-800:]}, 500)
                return
            # A versioned save may have started a local refresh during the scan.
            # Queue one more sync against the new snapshot, without blocking saves
            # for the duration of the subprocess or replacing any running job.
            with self.server.assets.lock:
                if self.server.assets.status()["job"]["running"]:
                    self.server.assets.refresh()
                    job = self.server.assets.status()["job"]
                    job["refreshQueued"] = True
                else:
                    job = self.server.assets.start(embeddings=False, confirm_cloud=False)
        self._json({"ok": True, "data": {"stdout": r.stdout[-2000:], "job": job}},
                   200 if self.path == "/api/rescan" else 202)

    def _section_save(self, req):
        """Replace only a server-parsed versioned section, preserving other bytes."""
        path = req.get("path", "")
        store = self.server.assets.file_store
        store.entry(path, write=True)
        current = store.read(path)
        if not current["editable"]:
            raise FileProblem(current["readOnlyReason"], 403)
        if os.path.splitext(path)[1].lower() not in (".md", ".markdown"):
            raise FileProblem("仅 Markdown 文件支持组件编辑")
        base = req.get("baseVersion", req.get("baseSha256"))
        if not base:
            raise FileProblem("缺少文件版本，请重新加载组件", 428)
        if base != current["sha256"]:
            raise SectionConflict("文件内容已改变，请重新加载组件")
        text = req.get("text", "")
        if not isinstance(text, str):
            raise FileProblem("组件内容必须是文本")
        raw = current["content"].encode("utf-8")
        section_id = req.get("sectionId")
        if not section_id and "baseSha256" in req and "baseVersion" not in req:
            # Compatibility for the concurrently shipped asset UI: mandatory
            # strong base hash and an exact server-parsed range, not arbitrary lines.
            match = next((s for s in snapshot(raw)["sections"] if
                          s["start_line"] == req.get("startLine") and s["end_line"] == req.get("endLine")), None)
            section_id = match["id"] if match else None
        if not section_id:
            raise FileProblem("缺少有效组件 ID，请刷新页面", 428)
        changed = replace_section(raw, base, section_id, text)
        result = store.save(path, changed.decode("utf-8"), base, source="section:" + section_id)
        if result["changed"]:
            self.server.assets.refresh()
        self._json({"ok": True, "data": result})

    def _open(self, req):
        entry = self.server.assets.file_store.entry(req.get("path", ""), write=True)
        real = entry["path"]
        command = ["open", "-t", real] if sys.platform == "darwin" else ["xdg-open", real]
        result = subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode:
            raise FileProblem("系统编辑器未能打开文件", 500)
        self._json({"ok": True, "data": {"opened": real}})

    def _sync_dups(self, req):
        """Sync the snapshot's duplicate group without overwriting external edits."""
        store = self.server.assets.file_store
        source = store.read(req.get("source", ""))
        real_src = source["path"]
        store.entry(real_src, write=True)
        idx = load_index() or {"files": []}
        rec = next((f for f in idx["files"] if os.path.realpath(f["path"]) == real_src), None)
        if not rec:
            raise FileProblem("源文件不在指令索引中", 403)
        group = [f for f in idx["files"] if f.get("sha") == rec["sha"] and os.path.realpath(f["path"]) != real_src]
        if not group:
            raise FileProblem("该文件没有同内容副本（编辑后请直接同步，无需重新扫描）", 404)
        results, errors = [], []
        for target in group:
            path = os.path.realpath(target["path"])
            try:
                old = store.read(path)
                if hashlib.sha1(old["content"].encode("utf-8")).hexdigest() != target["sha"]:
                    raise FileProblem("副本已被修改，跳过以免覆盖", 409)
                result = store.save(path, source["content"], old["sha256"], source="sync:" + real_src)
                results.append(dict(result, path=path))
            except FileProblem as exc:
                errors.append({"path": path, "error": str(exc)})
        if results:
            self.server.assets.refresh()
        self._json({"ok": True, "data": {"source": real_src, "synced": results,
                                         "errors": errors}})

    def _translate(self, req):
        """调用本机已配置的 LLM 翻译文件内容（分段 + 磁盘缓存）。
        密钥来源优先级：环境变量 → ~/.hermes/.env 的 GLM_* / OPENAI_*。"""
        import hashlib, re as _re
        file_data = self.server.assets.file_store.read(req.get("path", ""))
        if not file_data["editable"]:
            raise FileProblem("只读文件不发送至翻译服务", 403)
        path = file_data["path"]
        lang = req.get("lang", "中文")
        src = file_data["content"]

        # ── 读取 ~/.hermes/.env 的 KEY=VALUE（不覆盖已有环境变量）──
        env = dict(os.environ)
        envf = os.path.expanduser("~/.hermes/.env")
        if os.path.isfile(envf):
            with open(envf, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    env.setdefault(k.strip(), v.strip().strip("\"'"))

        # ── 模型解析：Hermes config.yaml 的 default/model + provider ──
        provider, model, base = "glm", "glm-4.7-flash", ""
        cfg = os.path.expanduser("~/.hermes/config.yaml")
        if os.path.isfile(cfg):
            try:
                text = open(cfg, encoding="utf-8", errors="replace").read()
                m = _re.search(r"^model:\s*$", text, _re.M)
                if m:
                    blk = text[m.end():m.end() + 600]
                    mm = _re.search(r"^\s+default:\s*(\S+)", blk, _re.M)
                    if mm: model = mm.group(1).strip().strip("\"'")
                    mm = _re.search(r"^\s+provider:\s*(\S+)", blk, _re.M)
                    if mm: provider = mm.group(1).strip().strip("\"'")
                    mm = _re.search(r"^\s+base_url:\s*(\S+)", blk, _re.M)
                    if mm: base = mm.group(1).strip().strip("\"'")
            except OSError:
                pass

        # ── 根据模型名/来源选择调用方式 ──
        key = ""
        if model.startswith("glm") or provider in ("zai", "glm", "auto"):
            if provider == "auto" or model.startswith("glm") or provider in ("zai", "glm"):
                provider = "glm"
                base = env.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
                key = env.get("GLM_API_KEY", "")
                if not model.startswith("glm"):
                    model = "glm-5.3-flashx"   # GLM 代理上没有 anthropic/* 等外部模型名
        if not key:
            if provider in ("openrouter", "auto") and env.get("OPENROUTER_API_KEY"):
                provider, base = "openrouter", "https://openrouter.ai/api/v1"
                key = env["OPENROUTER_API_KEY"]
                if model.startswith("glm"):
                    model = "glm-5.3-flash"
            elif env.get("OPENAI_API_KEY"):
                provider = "openai"
                base = env.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
                key = env["OPENAI_API_KEY"]
        if not key and provider != "ollama" and env.get("GLM_API_KEY"):
            # 配置指向的 provider（如 openai-codex OAuth）没有可用 API key 时，回退到 GLM
            provider = "glm"
            base = env.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
            key = env["GLM_API_KEY"]
            model = "glm-5.3-flashx"   # GLM 代理上没有 gpt-* 等外部模型名
        if not key and provider != "ollama":
            self._json({"ok": False, "error":
                        "未找到可用 API key：请设置 GLM_API_KEY / OPENROUTER_API_KEY / OPENAI_API_KEY"
                        "（环境变量或 ~/.hermes/.env）"}, 400)
            return
        # 模型回退链：代理上模型名不可用时逐个尝试（glm-5.3-flash 便宜快，逐步降级）
        MODEL_FALLBACKS = {"glm": ["glm-5.3-flashx", "glm-5.3-flash", "glm-5.3", "glm-5.2", "glm-5"]}
        model_candidates = [model] + [m for m in MODEL_FALLBACKS.get(provider, []) if m != model]

        # ── 分段（3000 字符，按行边界切）──
        chunks, buf = [], ""
        for line in src.splitlines(keepends=True):
            if len(buf) + len(line) > 3000 and buf:
                chunks.append(buf); buf = ""
            buf += line
        if buf: chunks.append(buf)
        chunks = chunks or [""]
        # ── 缓存 ──
        cache_dir = os.path.expanduser("~/.hermes/cache/scratch/agent-atlas-tr-cache")
        os.makedirs(cache_dir, exist_ok=True)
        def ckey(c): return hashlib.sha1((lang + "\x00" + c).encode()).hexdigest()[:24]

        def call_llm(chunk):
            sys_p = ("你是专业技术翻译。将用户给出的 Markdown 内容准确翻译成%s。" % lang +
                     "严格保留 Markdown 结构（标题/列表/表格）、代码块内容、命令、文件路径与链接原样不译。"
                     "只输出译文，不要任何解释。")
            if provider == "ollama":
                url = "http://127.0.0.1:11434/api/chat"
                payload = {"model": model, "stream": False, "messages": [
                    {"role": "system", "content": sys_p},
                    {"role": "user", "content": chunk}]}
            else:
                url = base.rstrip("/") + "/chat/completions"
                payload = {"model": model, "temperature": 0.1, "messages": [
                    {"role": "system", "content": sys_p},
                    {"role": "user", "content": chunk}]}
            headers = {"Content-Type": "application/json",
                       "User-Agent": "curl/8.4.0"}   # 上游 WAF 封禁 python-urllib 签名(error 1010)
            if key:
                headers["Authorization"] = "Bearer " + key
            last_err = None
            for m_try in (model_candidates if provider != "ollama" else [model]):
                if provider != "ollama":
                    payload["model"] = m_try
                req2 = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                              headers=headers)
                try:
                    with urllib.request.urlopen(req2, timeout=180) as r:
                        data = json.load(r)
                    if provider == "ollama":
                        return (data.get("message") or {}).get("content", ""), m_try
                    txt = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
                    if txt.strip():
                        return txt, m_try
                    raise RuntimeError("模型返回空内容")
                except urllib.error.HTTPError as e:
                    last_err = e
                    continue    # 换下一个候选模型
            raise last_err or RuntimeError("所有候选模型均失败")

        outs, translated_n, errors = [], 0, []
        used_model = model
        for i, c in enumerate(chunks):
            k = ckey(c)
            cpath = os.path.join(cache_dir, k + ".txt")
            if os.path.isfile(cpath):
                with open(cpath, encoding="utf-8") as fh:
                    outs.append(fh.read())
                continue
            try:
                t, m_used = call_llm(c)
                if not t.strip():
                    raise RuntimeError("模型返回空内容")
                with open(cpath, "w", encoding="utf-8") as fh:
                    fh.write(t)
                outs.append(t)
                used_model = m_used
                translated_n += 1
            except Exception as e:
                errors.append("第 %d/%d 段: %s" % (i + 1, len(chunks), str(e)[:160]))
                outs.append(c)      # 失败段保留原文
        self._json({"ok": True, "data": {
            "path": path, "lang": lang, "provider": provider, "model": used_model,
            "chunks": len(chunks), "translated": "\n".join(outs), "source": src,
            "fresh": translated_n, "errors": errors}})

    def log_message(self, fmt, *args):  # 安静一点
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % args))


def _provenance_lookup(assets):
    """Resolve a catalog path to its generation-mechanism evidence, on demand."""
    service = ProvenanceService(guide=getattr(assets, 'file_guide', None))

    def lookup(path):
        if not path or '\x00' in path or len(path) > 4096 or not os.path.isabs(path):
            raise FileProblem('文件路径无效')
        entry = next((f for f in assets.files() if f['path'] == path), None)
        if entry is None or entry.get('restricted'):
            raise FileProblem('文件不在可说明的扫描清单中', 403)
        return service.describe(entry)

    return lookup


def main():
    if not os.path.exists(os.path.join(WEB, "index.html")):
        print("缺少 web/index.html", file=sys.stderr)
        return 1
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.assets = AssetService(PROJECT, load_index)
    codex_home = os.environ.get("CODEX_HOME", os.path.join(HOME, ".codex"))
    srv.lifecycle = ReviewService(load_index, srv.assets.file_store,
                                  Store(os.path.join(PROJECT, ".agentatlas", "lifecycle")),
                                  os.path.join(codex_home, "sessions"), codex_home)
    srv.provenance = _provenance_lookup(srv.assets)
    srv.assets.refresh()
    print("AgentAtlas → http://%s:%d   (Ctrl-C 退出)" % (HOST, PORT))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
