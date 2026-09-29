from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from atlas.app.dependencies import Services, services
from atlas.core.pagination import PageQuery, paginate
from atlas.http.schemas import Body, Envelope, Page

router = APIRouter(tags=["duplicates"])


class DuplicateQuery(PageQuery):
    group: str = ""


class SyncRequest(Body):
    source: str = Field(min_length=1, max_length=4096)
    baseVersion: str = Field(min_length=64, max_length=64)


@router.get("/duplicates", response_model=Envelope[Page])
def groups(query: Annotated[DuplicateQuery, Query()], ctx: Services = Depends(services)):
    groups = [g for g in ctx.duplicates.groups() if not query.group or g["id"] == query.group]
    return {"ok": True, "data": paginate(groups, query, ctx.assets.version)}


@router.post("/sync-dups")
def sync(request: SyncRequest, ctx: Services = Depends(services)):
    result = ctx.duplicates.sync(request.source, request.baseVersion)
    if result["synced"]:
        result["refresh"] = ctx.jobs.refresh(lambda: ctx.workflows.refresh(rescan=True))
    return {"ok": True, "data": result}
