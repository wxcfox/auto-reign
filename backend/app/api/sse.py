from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException


def sse_event(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def http_error_payload(error: HTTPException) -> dict[str, Any]:
    if isinstance(error.detail, dict):
        return {
            "code": error.detail.get("code", "request_failed"),
            "message": error.detail.get("message", "Request failed."),
            "status_code": error.status_code,
        }
    return {
        "code": "request_failed",
        "message": str(error.detail),
        "status_code": error.status_code,
    }
