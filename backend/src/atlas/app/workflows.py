import json
from pathlib import Path

from atlas.core.atomic import atomic_write
from atlas.files.sections import replace_section, snapshot
from atlas.files.service import FileProblem
from atlas.instructions.scanner import scan
from atlas.assets.catalog import read_text


class Workflows:
    def __init__(self, config, assets, files, index, jobs, settings):
        self.config = config
        self.assets = assets
        self.files = files
        self.index = index
        self.jobs = jobs
        self.settings = settings

    def refresh(self, rescan=False, embedding_settings=None, max_chunks=256):
        if rescan or not self.config.instruction_path.exists():
            self.jobs.progress("instructions")
            result = scan(self.config.scan_roots, self.config.home, self.config.max_depth)
            atomic_write(self.config.instruction_path, json.dumps(result, ensure_ascii=False).encode())
        self.jobs.progress("discover")
        catalog = self.assets.refresh()
        self.jobs.progress("keyword")
        result = self.index.sync([entry for entry in catalog["files"] if entry.get("searchable")], read_text)
        if embedding_settings is not None:
            self.jobs.progress("embedding")
            result["embedding"] = self.index.embed_pending(embedding_settings, max_chunks=max_chunks,
                                                           progress=lambda values: self.jobs.progress(progress=values))
        return result

    def save(self, request):
        result = self.files.save(request.path, request.content, request.baseVersion)
        if result["changed"]:
            result["refresh"] = self.jobs.refresh(lambda: self.refresh(rescan=True))
        return result

    def section_save(self, request):
        document = self.files.read(request.path)
        self.files.entry(request.path, write=True)
        if not document["editable"] or Path(request.path).suffix.lower() not in {".md", ".markdown"}:
            raise FileProblem("当前文件不支持 Markdown 章节编辑", 403)
        raw = document["content"].encode()
        changed = replace_section(raw, request.baseVersion, request.sectionId, request.text)
        result = self.files.save(request.path, changed.decode(), request.baseVersion,
                                 source="section:" + request.sectionId)
        if result["changed"]:
            result["refresh"] = self.jobs.refresh(lambda: self.refresh(rescan=True))
        return result

    def sections(self, path):
        document = self.files.read(path)
        if document["truncated"] or document["sha256"] is None:
            raise FileProblem("截断内容或无效编码不支持章节编辑", 413)
        return dict(snapshot(document["content"].encode()), path=path, editable=document["editable"])

    def start_index(self, request):
        settings = None
        if request.embeddings:
            if not request.confirmCloud:
                raise FileProblem("请确认向量索引的云端发送范围")
            settings = self.settings.authorized()
            if not all(settings.get(key) for key in ("baseUrl", "model", "apiKey", "categories")):
                raise FileProblem("请完整配置 embedding 服务与发送范围")
        return self.jobs.start("index", lambda: self.refresh(embedding_settings=settings, max_chunks=request.maxChunks))
