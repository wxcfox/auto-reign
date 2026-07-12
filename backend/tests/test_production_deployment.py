from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = ROOT / "deploy"


def test_local_compose_only_contains_development_dependencies() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert set(compose["services"]) == {"mysql", "qdrant"}
    assert set(compose["volumes"]) == {"mysql_data", "qdrant_data"}


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


def test_release_workflow_publishes_main_as_an_explicit_version() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert "  workflow_dispatch:" in workflow
    assert "\n  push:" not in workflow
    assert "ref: main" in workflow
    assert "auto-reign-${{ matrix.component }}:${{ needs.metadata.outputs.version }}" in workflow
    assert (
        "auto-reign-${{ matrix.component }}:sha-${{ needs.metadata.outputs.source_sha }}"
        in workflow
    )
    assert "auto-reign-${{ matrix.component }}:latest" not in workflow
    assert workflow.index("docker/build-push-action") < workflow.index("git tag --annotate")
    assert workflow.count("git rev-list -n 1") == 2
    assert "Reusing existing tag v$VERSION after an incomplete release." in workflow
    assert 'if [[ "$tag_commit" != "$SOURCE_SHA" ]]' in workflow


def test_repository_does_not_deploy_production_from_github_actions() -> None:
    assert not (ROOT / ".github" / "workflows" / "deploy-production.yml").exists()


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
