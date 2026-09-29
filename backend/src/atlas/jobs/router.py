from fastapi import APIRouter, Depends
from pydantic import Field

from atlas.app.dependencies import Services, services
from atlas.http.schemas import Body

router = APIRouter(tags=["jobs"])


class IndexRequest(Body):
    embeddings: bool = False
    confirmCloud: bool = False
    maxChunks: int = Field(default=256, ge=1, le=4096)


@router.post("/rescan", status_code=202)
@router.post("/assets/rescan", status_code=202)
def rescan(request: Body | None = None, ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.jobs.start("rescan", lambda: ctx.workflows.refresh(rescan=True))}


@router.post("/search/index", status_code=202)
def index(request: IndexRequest, ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.workflows.start_index(request)}
