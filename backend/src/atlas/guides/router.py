from fastapi import APIRouter, Depends, Query

from atlas.app.dependencies import Services, services
from atlas.files.service import FileProblem

router = APIRouter(tags=["guides"])


def guide_entry(ctx, path):
    entry = next((entry for entry in ctx.assets.files() if entry["path"] == path), None)
    if entry is None or entry.get("restricted"):
        raise FileProblem("文件不在可说明的清单中", 403)
    return entry


@router.get("/file-guide")
def guide(path: str | None = Query(default=None, min_length=1, max_length=4096), ctx: Services = Depends(services)):
    data = ctx.guide.library() if path is None else dict(ctx.guide.describe(guide_entry(ctx, path), home=ctx.config.home), path=path)
    return {"ok": True, "data": data}


@router.get("/provenance")
def provenance(path: str = Query(min_length=1, max_length=4096), ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.provenance.describe(guide_entry(ctx, path))}
