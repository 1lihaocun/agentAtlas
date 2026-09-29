from typing import Annotated

from fastapi import APIRouter, Depends, Query

from atlas.app.dependencies import Services, services
from atlas.memories.schemas import MemoryQuery
from atlas.http.schemas import Envelope, Page

router = APIRouter(tags=["memories"])


@router.get("/memories", response_model=Envelope[Page])
def listing(query: Annotated[MemoryQuery, Query()], ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.memories.listing(query)}
