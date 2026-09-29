import hashlib
import os

from atlas.files.service import FileProblem


class DuplicateService:
    def __init__(self, instruction_loader, files):
        self.instruction_loader = instruction_loader
        self.files = files

    def groups(self):
        index = self.instruction_loader() or {"files": [], "dupGroups": []}
        rows = {f["path"]: f for f in index["files"]}
        return [{"id": rows[group[0]]["sha"], "files": [rows[path] for path in group]}
                for group in index["dupGroups"]]

    def sync(self, source_path, expected_version):
        source = self.files.read(source_path)
        self.files.entry(source["path"], write=True)
        if source["sha256"] is None:
            raise FileProblem("源文件不能同步：" + source["readOnlyReason"], 415)
        if source["sha256"] != expected_version:
            raise FileProblem("源文件已经变化，请重新打开后同步", 409)
        index = self.instruction_loader() or {"files": []}
        record = next((row for row in index["files"] if row["path"] == source["path"]), None)
        if record is None:
            raise FileProblem("源文件不在指令清单中", 403)
        targets = [row for row in index["files"] if row["sha"] == record["sha"]
                   and os.path.realpath(row["path"]) != source["path"]]
        if not targets:
            raise FileProblem("当前文件没有重复副本", 404)
        results = []
        for target in targets:
            try:
                current = self.files.read(target["path"])
                if current["sha256"] is None:
                    raise FileProblem("目标文件不能同步：" + current["readOnlyReason"], 415)
                if hashlib.sha1(current["content"].encode()).hexdigest() != target["sha"]:
                    raise FileProblem("目标文件已经变化，停止写入该文件", 409)
                saved = self.files.save(target["path"], source["content"], current["sha256"],
                                        source="sync:" + source["path"])
                results.append(dict(saved, path=target["path"], ok=True))
            except FileProblem as error:
                results.append({"path": target["path"], "ok": False, "error": str(error),
                                "status": error.status})
        return {"source": source["path"], "results": results,
                "synced": sum(row["ok"] for row in results),
                "failed": sum(not row["ok"] for row in results)}
