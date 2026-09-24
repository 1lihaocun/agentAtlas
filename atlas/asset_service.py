"""Asset inventory and local indexing jobs; cloud work is always explicit."""
import copy
import json
import os
from pathlib import Path
import threading
import time

try:
    from .asset_files import FileStore, FileProblem
    from .asset_settings import SettingsStore, atomic_write
    from .memory_metadata import MemoryMetadata
    from .memory_storage import build_memory_storage
    from .file_guide import FileGuide
    from .project_identity import project_candidates, safe_path
except ImportError:
    from asset_files import FileStore, FileProblem
    from asset_settings import SettingsStore, atomic_write
    from memory_metadata import MemoryMetadata
    from memory_storage import build_memory_storage
    from file_guide import FileGuide
    from project_identity import project_candidates, safe_path


class AssetService:
    def __init__(self, project, instruction_loader, discover_fn=None, reader=None, index=None):
        self.directory = Path(project) / ".agentatlas"
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.catalog_path = self.directory / "assets.json"
        self.settings = SettingsStore(self.directory)
        self.instruction_loader = instruction_loader
        self._configured_discovery = discover_fn is None
        if discover_fn is None or reader is None:
            try:
                from .catalog import discover, read_text
            except ImportError:
                from catalog import discover, read_text
            discover_fn, reader = discover_fn or discover, reader or read_text
        if index is None:
            try:
                from .search_index import SearchIndex
            except ImportError:
                from search_index import SearchIndex
            index = SearchIndex(str(self.directory / "search.sqlite3"))
        self.discover, self.reader, self.index = discover_fn, reader, index
        self.lock = threading.RLock()
        self.worker = None
        self.refresh_requested = False
        self.job = {"running": False, "stage": "idle", "error": None}
        self.catalog = {"files": [], "totalFiles": 0, "counts": {"categories": {}, "platforms": {}}}
        if self.catalog_path.exists():
            try:
                self.catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.job["error"] = "文件库快照损坏，请重新扫描"
        self.file_store = FileStore(self.files, self.directory / "backups")
        self.memory_metadata = MemoryMetadata()
        self.file_guide = FileGuide()

    def files(self):
        with self.lock:
            # The catalog is the sole authorization source, including first startup.
            # Legacy instruction metadata has not passed profile/symlink policy.
            return list(self.catalog["files"])

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.catalog)

    def file_guides(self, path=None):
        entry = None
        if path is not None:
            if not isinstance(path, str) or not path or '\x00' in path or not Path(path).is_absolute() or '..' in Path(path).parts or str(Path(path)) != path:
                raise FileProblem('文件路径无效')
            # Metadata-only records may be described, but this never reads their body
            # or grants the FileStore permission to open them.
            entry = next((f for f in self.files() if f['path'] == path), None)
            if entry is None or entry.get('restricted'):
                raise FileProblem('文件不在可说明的扫描清单中', 403)
        try:
            if entry is None:
                return self.file_guide.library()
            return dict(self.file_guide.describe(entry), path=path)
        except (OSError, ValueError, KeyError, TypeError):
            raise FileProblem('文件说明库暂不可用，请检查 docs/file-types.json', 503) from None

    def memories(self, project="", platform="", profile=""):
        try:
            from .memory_scope import build_memory_view
        except ImportError:
            from memory_scope import build_memory_view
        with self.lock:
            files = copy.deepcopy(self.catalog["files"])
            candidates = copy.deepcopy(self.catalog.get('projectCandidates'))
            home = Path(self.catalog.get('home') or Path.home())
        # Upgrade pre-candidate snapshots from actual instruction paths, never
        # from root/proj/sub display labels. This cannot authorize file reads.
        if candidates is None:
            instructions = (self.instruction_loader() or {}).get('files', [])
            candidates = project_candidates(instructions, home=home)
        roots = sorted({row['path'] for row in candidates if isinstance(row, dict)
                        and row.get('status') == 'candidate'
                        and safe_path(row.get('path'), home) is not None})
        metadata, evidence = self.memory_metadata.collect(files, roots)
        result = build_memory_view(files, project_roots=roots, home=home,
                                   project=project, platform=platform, profile=profile,
                                   extension_evidence=evidence)
        result['projectCandidates'] = candidates
        result['storageTree'] = build_memory_storage(files, selected_paths={f['path'] for f in result.get('files', [])})
        for entry in result.get('files', []):
            if entry['path'] in metadata:
                entry['memoryInfo'] = metadata[entry['path']]
            if entry['path'] in result['storageTree']['locations']:
                entry['memoryStorage'] = result['storageTree']['locations'][entry['path']]
        return result

    def status(self):
        with self.lock:
            job = copy.deepcopy(self.job)
            skipped = sum(not f.get("searchable", False) for f in self.catalog["files"])
        index = self.index.status()
        index.update(skippedFiles=skipped, errors=job.get("localResult", {}).get("errors", 0))
        return {"job": job, "index": index, "available": self.catalog_path.exists()}

    def _progress(self, *args, **kwargs):
        with self.lock:
            self.job["progress"] = kwargs or (args[0] if len(args) == 1 else list(args))

    def start(self, embeddings=False, confirm_cloud=False, max_chunks=256):
        if embeddings and not confirm_cloud:
            raise FileProblem("向量化将向云端发送所选类别正文，请先明确确认", 400)
        if not isinstance(max_chunks, int) or isinstance(max_chunks, bool) or not 1 <= max_chunks <= 4096:
            raise FileProblem("每次向量化段数必须在 1–4096 之间")
        settings = None
        if embeddings:
            settings = self.settings.authorized()
            if not settings.get("baseUrl") or not settings.get("model") or not settings.get("apiKey"):
                raise FileProblem("请先在检索设置中配置 embedding 地址、模型和 API Key")
            if not settings.get("categories"):
                raise FileProblem("请至少选择一个允许发送到云端的文件类别")
        with self.lock:
            if self.job["running"]:
                raise FileProblem("已有索引任务运行中，请稍后重试", 409)
            self.job = {"running": True, "stage": "discover", "error": None,
                        "startedAt": time.time(), "embeddings": embeddings}
            self.worker = threading.Thread(target=self._run, args=(settings, max_chunks), daemon=True)
            self.worker.start()
            return copy.deepcopy(self.job)

    def refresh(self):
        with self.lock:
            if self.job["running"]:
                self.refresh_requested = True
                return
            self.start()

    def _sync(self):
        with self.lock:
            self.job["stage"] = "discover"
        instruction_snapshot = self.instruction_loader() or {}
        instructions = instruction_snapshot.get("files", [])
        options = {}
        if self._configured_discovery:
            try:
                from . import scan
            except ImportError:
                import scan
            options = dict(home=scan.HOME, scan_roots=scan.ROOTS, max_depth=scan.DEFAULT_DEPTH)
        catalog = self.discover(instruction_files=instructions, **options)
        catalog['instructionGeneratedAt'] = instruction_snapshot.get('generatedAt')
        atomic_write(self.catalog_path, json.dumps(catalog, ensure_ascii=False).encode("utf-8"))
        with self.lock:
            self.catalog = catalog
            self.job["stage"] = "keyword"
        stats = self.index.sync([f for f in catalog["files"] if f.get("searchable", False)], self.reader)
        with self.lock:
            self.job["localResult"] = stats

    def _run(self, settings, max_chunks):
        try:
            self._sync()
            if settings is not None:
                with self.lock:
                    self.job["stage"] = "embedding"
                result = self.index.embed_pending(settings, max_chunks=max_chunks, progress=self._progress)
                with self.lock:
                    self.job["embeddingResult"] = result
            while True:
                with self.lock:
                    if not self.refresh_requested:
                        self.job.update(running=False, stage="done", finishedAt=time.time())
                        break
                    self.refresh_requested = False
                self._sync()
        except Exception as exc:
            # Do not expose provider response bodies or credentials in job messages.
            with self.lock:
                detail = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                self.job.update(running=False, stage="error", error="索引任务失败：%s" % detail)

    def search(self, query, mode="keyword", filters=None, limit=50):
        if not query.strip():
            return {"results": [], "total": 0, "mode": mode}
        if len(query) > 2000:
            raise FileProblem("搜索文本过长（最多 2000 字符）")
        settings = self.settings.authorized() if mode in ("vector", "hybrid") else None
        return self.index.search(query, mode=mode, filters=filters, limit=limit, settings=settings)
