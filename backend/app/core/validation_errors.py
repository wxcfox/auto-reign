from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status


def remove_validation_inputs(value: object) -> object:
    """Remove request input values while preserving validation diagnostics."""
    if isinstance(value, dict):
        return {
            key: remove_validation_inputs(item)
            for key, item in value.items()
            if key != "input"
        }
    if isinstance(value, list):
        return [remove_validation_inputs(item) for item in value]
    if isinstance(value, tuple):
        return tuple(remove_validation_inputs(item) for item in value)
    return value


async def request_validation_error_handler(
    _request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    detail = remove_validation_inputs(error.errors())
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=jsonable_encoder({"detail": detail}),
    )
