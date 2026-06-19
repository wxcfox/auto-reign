from fastapi.testclient import TestClient


def test_health_reports_local_dependencies(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["storage"]["sqlite"] == "configured"
    assert body["storage"]["chroma"] == "configured"
    assert "providers" in body
