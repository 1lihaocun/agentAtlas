from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    ok: bool = True
    data: T


class Page(BaseModel):
    model_config = ConfigDict(extra="allow")
    items: list[dict[str, Any]]
    total: int
    page: int
    pageSize: int
    catalogVersion: str | None = None


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FilePath(Body):
    path: str = Field(min_length=1, max_length=4096)
