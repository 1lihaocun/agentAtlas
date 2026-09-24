"""Small lifecycle interface shared by HTTP and isolated integration tests."""
import os
from pathlib import Path
import time

try:
    from .asset_files import FileProblem
    from .retirement import build_retirement
    from .usage import compute_usage
    from .storage import Conflict, PreconditionRequired
    from .effective import resolve_codex, find_project_root
except ImportError:  # python3 atlas/serve.py
    from asset_files import FileProblem
    from retirement import build_retirement
    from usage import compute_usage
    from storage import Conflict, PreconditionRequired
    from effective import resolve_codex, find_project_root


class ReviewService:
    def __init__(self, index_loader, files, store, source_root, codex_home, clock=time.time):
        self.index_loader = index_loader
        self.files = files
        self.store = store
        self.source_root = Path(source_root)
        self.codex_home = Path(codex_home)
        self.clock = clock

    def index(self):
        index = self.index_loader()
        if index is None:
            raise FileProblem("尚未扫描指令文件，请先重新扫描", 404)
        return index

    def usage(self, days=30, index=None):
        return compute_usage(self.index() if index is None else index, self.source_root, self.codex_home,
                             days=days, now=self.clock(), cache_dir=self.store.state_dir)

    def queue(self, days=30, stale_days=90):
        index = self.index()
        evidence = self.usage(days, index=index)
        return build_retirement(index, evidence, self.store, self.clock(), days, stale_days)

    def decide(self, path, version, action, snooze_days=None):
        entries = {os.path.abspath(row["path"]): row for row in self.index()["files"]}
        # FileStore applies the asset scanner's sensitive-path/read-only policy.
        self.files.entry(path, write=True)
        path = os.path.abspath(path)
        entry = entries.get(path)
        if entry is None:
            raise FileProblem("文件不在指令索引中", 403)
        if entry.get("scope") == "user" or entry.get("worktree"):
            raise FileProblem("全局指令及工作树副本不参与淘汰审阅", 403)
        if not version:
            raise PreconditionRequired("缺少文件版本，请刷新后再决定")
        current = self.files.read(path)
        if not current["editable"]:
            raise FileProblem(current["readOnlyReason"], 403)
        if current["sha256"] != version:
            raise Conflict("文件内容已经变化，请重新审阅当前版本")
        return {"path": path, "version": version,
                "decision": self.store.decide(path, version, action, self.clock(), snooze_days)}

    def effective(self, cwd):
        result = resolve_codex(cwd, find_project_root(cwd), self.codex_home)
        for row in result["files"]:
            try:
                self.files.entry(row["path"])
            except FileProblem:
                row["indexed"] = False
            else:
                row["indexed"] = True
        return result

    def evidence(self, path, days=30):
        self.files.entry(path)
        target = os.path.abspath(path)
        data = self.usage(days)
        row = next((row for row in data["files"] if row["path"] == target), None)
        if row is None:
            raise FileProblem("文件不在指令索引内", 403)
        details = [item for item in data["evidence"] if item["path"] == target]
        return {"path": target, "days": days, "tool": "Codex", "sourceStatus": row["sourceStatus"],
                "warnings": data["warnings"], "assumptions": data["assumptions"],
                "estimatedSessions": row["estimatedSessions"], "confirmedReads": row["confirmedReads"],
                "mentions": row["mentions"], "windowStart": data["windowStart"], "windowEnd": data["windowEnd"],
                "detailsReturned": len(details), "detailsTruncated": "evidence_details_limited" in data["warnings"],
                "evidence": details}
