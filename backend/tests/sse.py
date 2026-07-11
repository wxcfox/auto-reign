import json
from typing import Any

from fastapi.testclient import TestClient
from httpx import Response


def sse_event_data(response: Response, event: str) -> dict[str, Any]:
    assert response.status_code == 200
    for frame in response.text.strip().split("\n\n"):
        lines = frame.splitlines()
        if f"event: {event}" not in lines:
            continue
        data = "\n".join(
            line.removeprefix("data:").strip()
            for line in lines
            if line.startswith("data:")
        )
        return json.loads(data)
    raise AssertionError(f"SSE response did not include an {event} event.")


def sse_result(response: Response) -> dict[str, Any]:
    return sse_event_data(response, "result")


def sse_error(response: Response) -> dict[str, Any]:
    return sse_event_data(response, "error")


def post_sse(
    client: TestClient,
    path: str,
    *,
    json_body: object | None = None,
) -> dict[str, Any]:
    return sse_result(client.post(path, json=json_body))
