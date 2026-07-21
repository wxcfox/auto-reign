from pathlib import Path
import subprocess
import tomllib

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = ROOT / "deploy"


def test_local_compose_only_contains_development_dependencies() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert set(compose["services"]) == {"mysql", "qdrant", "elasticsearch"}
    assert set(compose["volumes"]) == {
        "mysql_data",
        "qdrant_data",
        "elasticsearch_data",
    }


def test_production_compose_only_exposes_loopback_application_ports() -> None:
    compose = yaml.safe_load((DEPLOY_DIR / "compose.prod.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert all("build" not in service for service in services.values())
    assert all(
        "ports" not in services[name]
        for name in ("mysql", "qdrant", "elasticsearch")
    )
    assert services["backend"]["ports"] == ["127.0.0.1:${AUTO_REIGN_BACKEND_PORT:-18300}:8000"]
    assert services["frontend"]["ports"] == ["127.0.0.1:${AUTO_REIGN_FRONTEND_PORT:-13100}:3000"]
    assert "caddy" not in services
    assert "${AUTO_REIGN_VERSION" in services["backend"]["image"]
    assert "${AUTO_REIGN_VERSION" in services["frontend"]["image"]
    assert services["qdrant"]["environment"]["QDRANT__SERVICE__API_KEY"] == (
        "${QDRANT_API_KEY:-}"
    )


def test_production_backend_enforces_single_instance_s3_object_storage() -> None:
    compose = yaml.safe_load((DEPLOY_DIR / "compose.prod.yml").read_text(encoding="utf-8"))
    environment = compose["x-backend-environment"]
    services = compose["services"]

    assert environment["APP_ENV"] == "production"
    assert environment["BACKEND_INSTANCE_COUNT"] == "1"
    assert environment["LOG_LEVEL"] == "${LOG_LEVEL:-INFO}"
    assert environment["OBJECT_STORE_BACKEND"] == "s3"
    assert environment["OBJECT_STORE_MAX_READ_BYTES"] == (
        "${OBJECT_STORE_MAX_READ_BYTES:-33554432}"
    )
    assert environment["S3_ADDRESSING_STYLE"] == "virtual"
    assert environment["S3_BUCKET"] == "${S3_BUCKET:?Set S3_BUCKET}"
    assert environment["S3_ENDPOINT_URL"] == (
        "${S3_ENDPOINT_URL:?Set S3_ENDPOINT_URL}"
    )
    assert environment["S3_REGION"] == "${S3_REGION:?Set S3_REGION}"
    assert environment["S3_NAMESPACE_APP_EXCLUSIVE"] == "true"
    assert environment["S3_ACCESS_KEY_ID"] == ("${S3_ACCESS_KEY_ID:?Set S3_ACCESS_KEY_ID}")
    assert environment["S3_SECRET_ACCESS_KEY"] == (
        "${S3_SECRET_ACCESS_KEY:?Set S3_SECRET_ACCESS_KEY}"
    )
    assert environment["S3_SESSION_TOKEN"] == "${S3_SESSION_TOKEN:-}"
    assert environment["S3_KEY_PREFIX"] == (
        "${S3_KEY_PREFIX:-auto-reign-production}"
    )
    assert services["migrate"]["environment"] == environment
    assert services["backend"]["environment"] == environment
    assert "deploy" not in services["backend"]
    assert "REGISTRATION_ENABLED" not in environment


def test_production_environment_example_has_no_s3_credential_defaults() -> None:
    values = _dotenv_values(DEPLOY_DIR / "auto-reign.env.example")

    assert values["APP_ENV"] == "production"
    assert values["BACKEND_INSTANCE_COUNT"] == "1"
    assert values["LOG_LEVEL"] == "INFO"
    assert values["OBJECT_STORE_BACKEND"] == "s3"
    assert values["S3_NAMESPACE_APP_EXCLUSIVE"] == "true"
    assert values["S3_ADDRESSING_STYLE"] == "virtual"
    assert values["S3_REGION"] == "cn-hangzhou"
    assert values["S3_ACCESS_KEY_ID"] == ""
    assert values["S3_SECRET_ACCESS_KEY"] == ""
    assert values["ELASTICSEARCH_PASSWORD"] == (
        "replace-with-a-long-random-password"
    )
    assert "REGISTRATION_ENABLED" not in values


def test_production_runtime_has_no_parallel_coordination_or_log_stack() -> None:
    compose = yaml.safe_load((DEPLOY_DIR / "compose.prod.yml").read_text(encoding="utf-8"))
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    project = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {
        dependency.split("[", maxsplit=1)[0].split("<", maxsplit=1)[0].split(">", maxsplit=1)[0]
        for dependency in project["project"]["dependencies"]
    }

    assert set(compose["services"]) == {
        "mysql",
        "qdrant",
        "elasticsearch",
        "migrate",
        "backend",
        "frontend",
    }
    assert set(compose["services"]).isdisjoint(
        {"redis", "kibana", "celery"}
    )
    assert dependencies.isdisjoint({"redis", "celery"})
    assert 'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]' in dockerfile
    assert "--workers" not in dockerfile


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


def _dotenv_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        assert separator == "=", raw_line
        values[key] = value
    return values
