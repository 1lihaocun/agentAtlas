import subprocess
import sys

from fastapi import APIRouter, Depends, Query

from atlas.app.dependencies import Services, services
from atlas.files.schemas import SaveFile, SaveSection
from atlas.files.service import FileProblem
from atlas.http.schemas import FilePath

router = APIRouter(tags=["files"])


@router.get("/file")
def read(path: str = Query(min_length=1, max_length=4096), ctx: Services = Depends(services)):
    data = ctx.files.read(path)
    return {"ok": True, "data": dict(data, version=data["sha256"])}


@router.get("/file/context")
def context(path: str = Query(min_length=1, max_length=4096), ctx: Services = Depends(services)):
    entry = ctx.files.entry(path)
    return {"ok": True, "data": {"memory": ctx.memories.file_context(path) if entry["category"] == "memory" else None,
                                "fileGuide": ctx.guide.describe(entry, home=ctx.config.home)}}


@router.post("/save")
def save(request: SaveFile, ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.workflows.save(request)}


@router.get("/sections")
def sections(path: str = Query(min_length=1, max_length=4096), ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.workflows.sections(path)}


@router.post("/section/save")
def save_section(request: SaveSection, ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.workflows.section_save(request)}


@router.post("/open")
def open_editor(request: FilePath, ctx: Services = Depends(services)):
    path = ctx.files.entry(request.path, write=True)["path"]
    command = ["open", "-t", path] if sys.platform == "darwin" else ["xdg-open", path]
    try:
        subprocess.run(command, check=True, capture_output=True)
    except OSError as error:
        raise FileProblem("无法启动外部编辑器；请安装并配置系统文件打开工具", 503) from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or b"").decode("utf-8", errors="replace").strip()[:1000]
        raise FileProblem(f"外部编辑器启动失败（退出码 {error.returncode}）：{detail}", 502) from error
    return {"ok": True, "data": {"opened": path}}
