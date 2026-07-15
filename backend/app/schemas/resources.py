from typing import Annotated, Literal

from pydantic import BaseModel, StringConstraints


ResourceListScope = Literal["visible", "owned", "global"]
ResourceScope = Literal["private", "global"]
ResourceId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=36),
]


class ResourceDeleteResponse(BaseModel):
    id: str
    status: Literal["deleted"]
