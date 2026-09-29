from fastapi import APIRouter, Depends
from pydantic import Field

from atlas.app.dependencies import Services, services
from atlas.http.schemas import FilePath

router = APIRouter(tags=["translation"])


class TranslationRequest(FilePath):
    baseVersion: str = Field(min_length=64, max_length=64)
    lang: str = Field(default="中文", min_length=1, max_length=50)
    confirmCloud: bool


@router.post("/translate")
def translate(request: TranslationRequest, ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.translation.translate(request)}
