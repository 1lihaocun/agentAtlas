from typing import Literal

from pydantic import Field

from atlas.core.pagination import PageQuery


class AssetQuery(PageQuery):
    platform: str = Field(default="", max_length=120)
    category: str = Field(default="", max_length=120)
    project: str = Field(default="", max_length=4096)
    q: str = Field(default="", max_length=2000)
    sort: Literal["path", "modified", "size"] = "path"
