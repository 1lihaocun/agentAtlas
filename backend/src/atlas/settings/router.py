from fastapi import APIRouter, Depends

from atlas.app.dependencies import Services, services

router = APIRouter(tags=["settings"])


@router.get("/settings")
def read(ctx: Services = Depends(services)):
    return {"ok": True, "data": ctx.settings.public()}
