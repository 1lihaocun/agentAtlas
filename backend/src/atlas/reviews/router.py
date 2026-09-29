from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from atlas.app.dependencies import Services, services
from atlas.core.pagination import PageQuery, paginate
from atlas.http.schemas import FilePath, Envelope, Page

router = APIRouter(tags=["reviews"])


class ReviewQuery(PageQuery):
    days: int = Field(default=30, ge=1, le=365)
    staleDays: int = Field(default=90, ge=1, le=3650)
    bucket: str = "review"


class Decision(FilePath):
    baseVersion: str = Field(min_length=64, max_length=64)
    action: Literal["keep", "snooze", "reset"]
    snoozeDays: int | None = Field(default=None, ge=1, le=365)


@router.get("/retirement", response_model=Envelope[Page])
def queue(query: Annotated[ReviewQuery, Query()], ctx: Services = Depends(services)):
    result = ctx.reviews.queue(query.days, query.staleDays)
    if query.bucket and query.bucket not in result["counts"]:
        raise ValueError("审阅分类无效")
    rows = [r for r in result.pop("rows") if not query.bucket or r["bucket"] == query.bucket]
    result.pop("total")
    return {"ok": True, "data": dict(paginate(rows, query, ctx.assets.version), **result)}


@router.post("/retirement/decision")
def decision(request: Decision, ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.reviews.decide(request.path, request.baseVersion, request.action, request.snoozeDays)}


@router.get("/usage")
def usage(days: int = Query(default=30, ge=1, le=365), ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.reviews.usage(days)}


@router.get("/usage/evidence")
@router.get("/usage/sessions")
def evidence(path: str = Query(min_length=1, max_length=4096), days: int = Query(default=30, ge=1, le=365),
             ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.reviews.evidence(path, days)}
