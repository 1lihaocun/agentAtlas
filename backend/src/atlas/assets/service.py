import copy
import json
import threading
import uuid

from atlas.assets.catalog import discover
from atlas.core.atomic import atomic_write
from atlas.instructions.scanner import ROOTS


class AssetService:
    def __init__(self, config, instruction_loader):
        self.config = config
        self.instruction_loader = instruction_loader
        self.catalog_path = config.state_dir / "assets.json"
        self.lock = threading.RLock()
        self.catalog = {"files": [], "totalFiles": 0, "counts": {"categories": {}, "platforms": {}}}
        if self.catalog_path.exists():
            try:
                catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
                if (not isinstance(catalog, dict) or not isinstance(catalog.get("files"), list)
                        or not isinstance(catalog.get("counts"), dict)
                        or any(not isinstance(row, dict) or any(not isinstance(row.get(key), str)
                               for key in ("path", "platform", "category")) for row in catalog["files"])):
                    raise ValueError("清单结构无效")
            except (OSError, ValueError) as error:
                raise ValueError(f"文件清单 {self.catalog_path} 无法加载；请修复文件或移走后重新扫描") from error
            self.catalog = catalog
        self.version = self.catalog.get("catalogVersion") or uuid.uuid4().hex

    def snapshot(self):
        with self.lock:
            return dict(copy.deepcopy(self.catalog), catalogVersion=self.version)

    def files(self):
        with self.lock:
            return copy.deepcopy(self.catalog["files"])

    def refresh(self):
        instructions = self.instruction_loader() or {}
        catalog = discover(home=self.config.home,
                           instruction_files=instructions.get("files", []),
                           scan_roots=ROOTS if self.config.scan_roots is None else self.config.scan_roots,
                           max_depth=self.config.max_depth)
        catalog["instructionGeneratedAt"] = instructions.get("generatedAt")
        catalog["catalogVersion"] = uuid.uuid4().hex
        atomic_write(self.catalog_path, json.dumps(catalog, ensure_ascii=False).encode("utf-8"))
        with self.lock:
            self.catalog = catalog
            self.version = catalog["catalogVersion"]
        return catalog

    def listing(self, query):
        with self.lock:
            return copy.deepcopy(self._listing(query))

    def _listing(self, query):
        from atlas.core.pagination import paginate

        snapshot = self.catalog
        files = snapshot["files"]
        terms = query.q.casefold().split()
        selected = [f for f in files if all(not getattr(query, key) or f.get(key) == getattr(query, key)
                    for key in ("platform", "category", "project"))
                    and all(term in f["path"].casefold() for term in terms)]
        if query.sort == "modified":
            selected.sort(key=lambda f: (-f.get("mtime", 0), f["path"]))
        elif query.sort == "size":
            selected.sort(key=lambda f: (-f.get("bytes", 0), f["path"]))
        else:
            selected.sort(key=lambda f: f["path"])
        return dict(paginate(selected, query, self.version),
                    counts=snapshot["counts"], generatedAt=snapshot.get("generatedAt"),
                    instructionGeneratedAt=snapshot.get("instructionGeneratedAt"),
                    facets={"platforms": sorted({f["platform"] for f in files}),
                            "categories": sorted({f["category"] for f in files}),
                            "projects": sorted({f["project"] for f in files if f.get("project")})})
