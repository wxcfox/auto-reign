from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
import re
from uuid import uuid4


_REQUEST_ID = ContextVar[str | None]("request_id", default=None)
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def is_safe_request_id(value: object) -> bool:
    return isinstance(value, str) and _SAFE_REQUEST_ID.fullmatch(value) is not None


def normalize_request_id(value: str | None) -> str:
    if is_safe_request_id(value):
        assert isinstance(value, str)
        return value
    return str(uuid4())


def bind_request_id(value: str) -> Token[str | None]:
    return _REQUEST_ID.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _REQUEST_ID.reset(token)


def get_request_id() -> str | None:
    return _REQUEST_ID.get()


@contextmanager
def request_id_context(value: str) -> Iterator[None]:
    token = bind_request_id(normalize_request_id(value))
    try:
        yield
    finally:
        reset_request_id(token)
