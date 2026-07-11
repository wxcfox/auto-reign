from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = ROOT / "deploy"


def test_production_compose_only_exposes_public_proxy_ports() -> None:
    compose = yaml.safe_load((DEPLOY_DIR / "compose.prod.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert all("build" not in service for service in services.values())
    assert all("ports" not in services[name] for name in ("mysql", "qdrant", "backend", "frontend"))
    assert services["caddy"]["ports"] == ["80:80", "443:443", "443:443/udp"]
    assert "${AUTO_REIGN_VERSION" in services["backend"]["image"]
    assert "${AUTO_REIGN_VERSION" in services["frontend"]["image"]


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
