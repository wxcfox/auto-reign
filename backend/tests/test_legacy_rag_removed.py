from fastapi.testclient import TestClient


def test_legacy_documents_api_is_not_registered(client: TestClient) -> None:
    response = client.get("/api/documents")

    assert response.status_code == 404


def test_legacy_rag_search_api_is_not_registered(client: TestClient) -> None:
    response = client.post("/api/rag/search", json={"query": "redis", "limit": 3})

    assert response.status_code == 404
