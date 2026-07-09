from __future__ import annotations

from importlib.resources import files
from pathlib import Path


DEFAULT_MANIFEST_FILENAME = "default_manifest.md"
DEFAULT_MANIFEST_EXAMPLE_FILENAME = "default_manifest.example.md"
_TEMPLATE_PACKAGE = "app.templates"


def packaged_default_manifest_body() -> str:
    return _strip_front_matter(
        files(_TEMPLATE_PACKAGE)
        .joinpath(DEFAULT_MANIFEST_EXAMPLE_FILENAME)
        .read_text(encoding="utf-8")
    )


def seed_default_manifest(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(packaged_default_manifest_body(), encoding="utf-8")


def read_default_manifest_body(path: Path | None) -> str:
    if path is not None and path.exists():
        return _strip_front_matter(path.read_text(encoding="utf-8"))
    return packaged_default_manifest_body()


def _strip_front_matter(raw: str) -> str:
    if not raw.startswith("---\n"):
        return raw
    marker = "\n---\n"
    end = raw.find(marker, 4)
    if end == -1:
        return raw
    return raw[end + len(marker) :]
