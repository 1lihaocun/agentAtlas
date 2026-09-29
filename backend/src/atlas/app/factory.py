from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
import portalocker
from starlette.exceptions import HTTPException

from atlas import __version__
from atlas.app.dependencies import Services
from atlas.core.config import AppConfig
from atlas.core.storage import StorageProblem
from atlas.files.sections import Conflict, InvalidUTF8
from atlas.files.service import FileProblem
from atlas.http.router import router
from atlas.http.security import LocalAccess


def create_app(config: AppConfig):
    @asynccontextmanager
    async def lifespan(app):
        config.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with portalocker.Lock(config.state_dir / "instance.lock", timeout=0):
            ctx = Services(config)
            app.state.services = ctx
            try:
                if config.refresh_on_start:
                    ctx.jobs.refresh(ctx.workflows.refresh)
                yield
            finally:
                ctx.jobs.close()

    app = FastAPI(title="AgentAtlas", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.add_middleware(LocalAccess, config=config)
    app.include_router(router)

    async def known_error(request, error):
        return JSONResponse({"ok": False, "error": str(error), "code": getattr(error, "code", "request_error"),
                             "changed": getattr(error, "changed", False)},
                            status_code=getattr(error, "status", 400))

    for kind in (FileProblem, StorageProblem, Conflict, InvalidUTF8, ValueError):
        app.add_exception_handler(kind, known_error)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        fields = [".".join(str(part) for part in item["loc"]) for item in error.errors()]
        return JSONResponse({"ok": False, "error": "参数无效：" + "、".join(fields)}, status_code=422)

    @app.exception_handler(HTTPException)
    async def http_error(request, error):
        return JSONResponse({"ok": False, "error": str(error.detail)}, status_code=error.status_code)

    @app.exception_handler(Exception)
    async def unexpected_error(request, error):
        logging.getLogger(__name__).exception("请求执行失败")
        return JSONResponse({"ok": False, "error": "请求执行失败：" + type(error).__name__}, status_code=500)

    @app.get("/api/health")
    def health():
        return {"ok": True, "data": {"version": __version__}}

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "接口不存在")
        root = config.static_dir.resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise HTTPException(403, "路径超出静态资源目录")
        if target.is_file():
            return FileResponse(target)
        if path.startswith("assets/") or (target.suffix and not path.startswith("file-types/")):
            raise HTTPException(404, "静态资源不存在")
        index = root / "index.html"
        if not index.is_file():
            raise HTTPException(503, "前端尚未构建，请执行前端构建命令")
        return FileResponse(index, headers={"Cache-Control": "no-store"})

    return app
