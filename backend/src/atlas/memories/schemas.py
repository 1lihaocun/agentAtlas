from pydantic import Field

from atlas.core.pagination import PageQuery


class MemoryQuery(PageQuery):
    project: str = Field(default="", max_length=4096)
    platform: str = Field(default="", max_length=120)
    profile: str = Field(default="", max_length=120)
    level: str = Field(default="", max_length=120)
    stage: str = Field(default="", max_length=120)
    q: str = Field(default="", max_length=2000)
    path: str = Field(default="", max_length=4096)
    recursive: bool = False
