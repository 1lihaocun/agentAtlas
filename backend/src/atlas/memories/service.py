import threading

from atlas.assets.identity import project_candidates, safe_path
from atlas.core.pagination import paginate
from atlas.files.service import FileProblem
from atlas.memories.metadata import MemoryMetadata
from atlas.memories.scope import build_memory_view
from atlas.memories.storage import build_memory_storage


class MemoryService:
    def __init__(self, assets, instruction_loader, home):
        self.assets = assets
        self.instruction_loader = instruction_loader
        self.home = home
        self.metadata = MemoryMetadata()
        self.lock = threading.RLock()
        self.cached = None

    def view(self):
        with self.lock:
            with self.assets.lock:
                version = self.assets.version
                if self.cached is not None and self.cached[0] == version:
                    return self.cached[1]
                snapshot = self.assets.snapshot()
            version = snapshot["catalogVersion"]
            files = snapshot["files"]
            candidates = snapshot.get("projectCandidates")
            if candidates is None:
                candidates = project_candidates((self.instruction_loader() or {}).get("files", []), home=self.home)
            roots = sorted({row["path"] for row in candidates if row.get("status") == "candidate"
                            and safe_path(row.get("path"), self.home) is not None})
            metadata, evidence = self.metadata.collect(files, roots)
            result = build_memory_view(files, project_roots=roots, home=self.home, extension_evidence=evidence)
            tree = build_memory_storage(files, home=self.home)
            for entry in result["files"]:
                entry["memoryInfo"] = metadata.get(entry["path"])
                entry["memoryStorage"] = tree["locations"].get(entry["path"])
            result.update(storageTree=tree, catalogVersion=version)
            self.cached = (version, result)
            return result

    def listing(self, query):
        data = self.view()
        terms = query.q.casefold().split()
        files = [entry for entry in data["files"]
                 if (not query.platform or entry["platform"] == query.platform)
                 and (not query.profile or entry.get("profile", "default") == query.profile)
                 and (not query.project or query.project in entry["semantics"]["appliesTo"])
                 and (not query.level or entry["semantics"]["level"] == query.level)
                 and (not query.stage or entry["semantics"].get("pipeline", {}).get("stage", "unknown") == query.stage)
                 and all(term in (entry["path"] + " " + (entry.get("memoryInfo") or {}).get("title", "")
                                  + " " + (entry.get("memoryInfo") or {}).get("description", "")).casefold() for term in terms)]
        tree = build_memory_storage(data["files"], home=self.home, selected_paths={f["path"] for f in files})
        matched = len(files)
        direct_count = matched
        subtree_count = matched
        if query.path:
            node = data["storageTree"]["nodes"].get(query.path)
            if node is None:
                raise FileProblem("目录不在当前记忆清单中", 404)
            filtered_node = tree["nodes"].get(query.path)
            direct_count = filtered_node["directCount"] if filtered_node else 0
            subtree_count = filtered_node["totalCount"] if filtered_node else 0
            included = set(node["filePaths"] if query.recursive else node["directFiles"])
            files = [f for f in files if f["path"] in included]
        files.sort(key=lambda f: f["path"])
        return dict(paginate(files, query, data["catalogVersion"]),
                    storageTree=tree, counts=dict(data["counts"], matched=matched,
                                                 direct=direct_count, subtree=subtree_count),
                    projects=data["projects"], platforms=data["platforms"], profiles=data["profiles"],
                    notice=data["notice"])

    def file_context(self, path):
        return next((entry for entry in self.view()["files"] if entry["path"] == path), None)
