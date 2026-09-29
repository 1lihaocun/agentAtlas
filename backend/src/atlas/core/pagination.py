from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PageQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(default=1, ge=1)
    pageSize: Literal[25, 50, 100] = 50

    @field_validator("pageSize", mode="before")
    @classmethod
    def query_integer(cls, value):
        return int(value) if isinstance(value, str) and value in {"25", "50", "100"} else value


def paginate(items, query, version=None):
    total = len(items)
    page = min(query.page, max(1, (total + query.pageSize - 1) // query.pageSize))
    start = (page - 1) * query.pageSize
    return {"items": items[start:start + query.pageSize], "total": total,
            "page": page, "pageSize": query.pageSize, "catalogVersion": version}
