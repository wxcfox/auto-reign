from fastapi.testclient import TestClient


def test_upload_markdown_analyzes_and_persists_document(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files={
            "file": (
                "resume.md",
                b"# Resume\n\nBuilt RAG systems with FastAPI.",
                "text/markdown",
            )
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source_filename"] == "resume.md"
    assert body["file_type"] == "markdown"
    assert body["title"]
    assert body["summary"]
    assert body["analysis_status"] == "completed"
    assert body["index_status"] in {"pending", "completed"}


def test_upload_rejects_pdf(client: TestClient) -> None:
    response = client.post(
        "/api/documents/upload",
        files={"file": ("resume.pdf", b"%PDF", "application/pdf")},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_file_type"
