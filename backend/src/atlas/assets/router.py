from typing import Annotated

from fastapi import APIRouter, Depends, Query

from atlas.app.dependencies import Services, services
from atlas.assets.schemas import AssetQuery
from atlas.http.schemas import Envelope, Page

router = APIRouter(tags=["assets"])


@router.get("/assets", response_model=Envelope[Page])
def listing(query: Annotated[AssetQuery, Query()], ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.assets.listing(query)}


@router.get("/assets/status")
def status(ctx: Services = Depends(services)):
    return {"ok": True, "data": {"job": ctx.jobs.status(), "index": ctx.index.status(),
                                "available": ctx.assets.catalog_path.is_file(),
                                "catalogVersion": ctx.assets.version}}
