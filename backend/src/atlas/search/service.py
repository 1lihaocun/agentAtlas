import hashlib
import json
import time
import uuid

from atlas.core.atomic import atomic_write
from atlas.core.pagination import paginate
from atlas.files.service import FileProblem
from atlas.search.index import _result


class SearchService:
    def __init__(self, index, settings, assets, directory):
        self.index = index
        self.settings = settings
        self.assets = assets
        self.directory = directory / "search-results"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def signature(self):
        payload = (self.assets.version, self.settings._revision())
        return hashlib.sha256(repr(payload).encode()).hexdigest()

    def keyword(self, query):
        filters = {key: getattr(query, key) for key in ("platform", "category", "project") if getattr(query, key)}
        if not query.q.strip():
            return dict(paginate([], query, self.assets.version), mode="keyword")
        rows, total = self.index._keyword_search(query.q.strip(), filters, query.pageSize, True,
                                                 (query.page - 1) * query.pageSize)
        page = min(query.page, max(1, (total + query.pageSize - 1) // query.pageSize))
        if page != query.page:
            rows = self.index._keyword_search(query.q.strip(), filters, query.pageSize,
                                              offset=(page - 1) * query.pageSize)
        return {"items": [_result(row, query.q.strip(), "keyword") for row in rows],
                "total": total, "page": page, "pageSize": query.pageSize,
                "catalogVersion": self.assets.version, "mode": "keyword"}

    def execute(self, request):
        if not request.confirmCloud:
            raise FileProblem("请确认发送检索文本到配置的 embedding 服务")
        signature = self.signature()
        settings = self.settings.authorized()
        result = self.index.search(request.q, mode=request.mode,
                                   filters=request.filters.model_dump(exclude_defaults=True),
                                   limit=2**31 - 1, settings=settings)
        if signature != self.signature():
            raise FileProblem("清单或授权已经变化，请重新提交检索", 409)
        identity = uuid.uuid4().hex
        result.update(searchId=identity, signature=signature, expiresAt=time.time() + 1800)
        atomic_write(self.directory / (identity + ".json"), json.dumps(result, ensure_ascii=False).encode())
        return {"searchId": identity, "total": result["total"], "expiresAt": result["expiresAt"]}

    def result(self, identity, query):
        if len(identity) != 32 or any(c not in "0123456789abcdef" for c in identity):
            raise FileProblem("检索结果标识无效")
        path = self.directory / (identity + ".json")
        if not path.is_file():
            raise FileProblem("检索结果不存在，请重新提交", 404)
        result = json.loads(path.read_text(encoding="utf-8"))
        if result["expiresAt"] < time.time() or result["signature"] != self.signature():
            path.unlink()
            raise FileProblem("检索结果已经过期，请重新提交", 410)
        return dict(paginate(result["results"], query, self.assets.version),
                    mode=result["mode"], searchId=identity, expiresAt=result["expiresAt"])
