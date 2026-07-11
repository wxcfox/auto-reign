from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = ROOT / "deploy"


def test_production_compose_only_exposes_loopback_application_ports() -> None:
    compose = yaml.safe_load((DEPLOY_DIR / "compose.prod.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert all("build" not in service for service in services.values())
    assert all("ports" not in services[name] for name in ("mysql", "qdrant"))
    assert services["backend"]["ports"] == [
        "127.0.0.1:${AUTO_REIGN_BACKEND_PORT:-18300}:8000"
    ]
    assert services["frontend"]["ports"] == [
        "127.0.0.1:${AUTO_REIGN_FRONTEND_PORT:-13100}:3000"
    ]
    assert "caddy" not in services
    assert "${AUTO_REIGN_VERSION" in services["backend"]["image"]
    assert "${AUTO_REIGN_VERSION" in services["frontend"]["image"]


def test_nginx_routes_api_and_frontend_to_loopback_ports() -> None:
    nginx = (DEPLOY_DIR / "nginx" / "auto-reign.conf").read_text(encoding="utf-8")

    assert "server_name auto-reign.agdoer.com;" in nginx
    assert "location /api/" in nginx
    assert "proxy_pass http://127.0.0.1:18300;" in nginx
    assert "proxy_pass http://127.0.0.1:13100;" in nginx
    assert "proxy_buffering off;" in nginx


def test_release_workflow_does_not_publish_latest_application_images() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "auto-reign-${{ matrix.component }}:${{ needs.metadata.outputs.version }}" in workflow
    assert "auto-reign-${{ matrix.component }}:sha-${{ github.sha }}" in workflow
    assert "auto-reign-${{ matrix.component }}:latest" not in workflow


def test_deploy_rejects_non_semver_before_reading_production_config() -> None:
    result = subprocess.run(
        [DEPLOY_DIR / "deploy.sh", "latest"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "version must match MAJOR.MINOR.PATCH" in result.stderr
    assert "environment file not found" not in result.stderr


def test_rollback_requires_explicit_confirmation() -> None:
    result = subprocess.run(
        [DEPLOY_DIR / "rollback.sh", "0.1.0"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "does not downgrade the MySQL schema" in result.stderr
