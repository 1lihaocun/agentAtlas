from pathlib import Path

from fastapi import APIRouter, Depends, Query

from atlas.app.dependencies import Services, services
from atlas.files.service import FileProblem

router = APIRouter(tags=["instructions"])


@router.get("/tree")
def tree(ctx: Services = Depends(services)):
    data = ctx.load_instructions()
    if data is None:
        raise FileProblem("尚未扫描指令文件，请执行重新扫描", 404)
    return {"ok": True, "data": data}


@router.get("/effective")
def effective(dir: str = Query(min_length=1, max_length=4096), ctx: Services = Depends(services)):
    path = Path(dir)
    if not path.is_absolute() or ".." in path.parts or path.resolve() != path or not path.is_dir():
        raise FileProblem("目录路径无效")
    if not path.is_relative_to(ctx.config.home):
        raise FileProblem("目录超出配置的用户目录", 403)
    return {"ok": True, "data": dict(ctx.reviews.effective(str(path)), dir=str(path), tool="Codex")}
