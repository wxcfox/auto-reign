from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str


class MessageResponse(BaseModel):
    message: str
