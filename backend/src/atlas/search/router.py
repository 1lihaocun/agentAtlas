from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from atlas.app.dependencies import Services, services
from atlas.assets.schemas import AssetQuery
from atlas.core.pagination import PageQuery
from atlas.http.schemas import Body, Envelope, Page

router = APIRouter(tags=["search"])


class Filters(Body):
    platform: str = ""
    category: str = ""
    project: str = ""


class SearchRequest(Body):
    q: str = Field(min_length=1, max_length=2000)
    mode: Literal["vector", "hybrid"]
    confirmCloud: bool
    filters: Filters = Field(default_factory=Filters)


@router.get("/search", response_model=Envelope[Page])
def keyword(query: Annotated[AssetQuery, Query()], ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.search.keyword(query)}


@router.post("/search/execute")
def execute(request: SearchRequest, ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.search.execute(request)}


@router.get("/search/results/{identity}", response_model=Envelope[Page])
def result(identity: str, query: Annotated[PageQuery, Query()], ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.search.result(identity, query)}
