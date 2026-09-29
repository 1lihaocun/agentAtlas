from fastapi import APIRouter

from atlas.assets import router as assets
from atlas.duplicates import router as duplicates
from atlas.files import router as files
from atlas.guides import router as guides
from atlas.instructions import router as instructions
from atlas.jobs import router as jobs
from atlas.memories import router as memories
from atlas.reviews import router as reviews
from atlas.search import router as search
from atlas.settings import router as settings
from atlas.translation import router as translation

router = APIRouter(prefix="/api")
for module in (assets, duplicates, files, guides, instructions, jobs, memories, reviews, search, settings, translation):
    router.include_router(module.router)
