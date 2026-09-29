from pydantic import Field

from atlas.http.schemas import FilePath


class SaveFile(FilePath):
    content: str
    baseVersion: str = Field(min_length=64, max_length=64)


class SaveSection(FilePath):
    text: str
    sectionId: str = Field(min_length=64, max_length=64)
    baseVersion: str = Field(min_length=64, max_length=64)
