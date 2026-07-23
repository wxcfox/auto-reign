from pathlib import Path
import os
import shlex
import subprocess
import tomllib

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = ROOT / "deploy"


def test_local_compose_only_contains_development_dependencies() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))

    assert set(compose["services"]) == {
        "redis",
        "mysql",
        "qdrant",
        "elasticsearch",
    }
    assert set(compose["volumes"]) == {
        "redis_data",
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
        for name in ("redis", "mysql", "qdrant", "elasticsearch")
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
    assert values["REDIS_IMAGE"] == "redis:7.4-alpine"
    assert values["AUTO_REIGN_REDIS_DIR"] == "/srv/auto-reign/redis"
    assert values["REDIS_URL"] == "redis://redis:6379/0"
    assert values["CHAT_STREAM_TTL_SECONDS"] == "3600"
    assert values["CHAT_STREAM_KEY_PREFIX"] == "auto_reign:chat"
    assert values["SOCKETIO_PING_INTERVAL_SECONDS"] == "25"
    assert values["SOCKETIO_PING_TIMEOUT_SECONDS"] == "20"
    assert "REGISTRATION_ENABLED" not in values


def test_production_compose_provides_redis_to_backend() -> None:
    compose = yaml.safe_load((DEPLOY_DIR / "compose.prod.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    redis = services["redis"]
    backend = services["backend"]

    assert redis["image"] == "${REDIS_IMAGE:-redis:7.4-alpine}"
    assert redis["volumes"] == [
        "${AUTO_REIGN_REDIS_DIR:?Set AUTO_REIGN_REDIS_DIR}:/data"
    ]
    assert redis["healthcheck"]["test"] == ["CMD", "redis-cli", "ping"]
    assert backend["environment"]["REDIS_URL"] == "redis://redis:6379/0"
    assert backend["environment"]["CHAT_STREAM_TTL_SECONDS"] == (
        "${CHAT_STREAM_TTL_SECONDS:-3600}"
    )
    assert backend["environment"]["CHAT_STREAM_KEY_PREFIX"] == (
        "${CHAT_STREAM_KEY_PREFIX:-auto_reign:chat}"
    )
    assert backend["environment"]["SOCKETIO_PING_INTERVAL_SECONDS"] == (
        "${SOCKETIO_PING_INTERVAL_SECONDS:-25}"
    )
    assert backend["environment"]["SOCKETIO_PING_TIMEOUT_SECONDS"] == (
        "${SOCKETIO_PING_TIMEOUT_SECONDS:-20}"
    )
    assert backend["depends_on"]["redis"]["condition"] == "service_healthy"


def test_backend_runtime_includes_socket_state_without_a_job_or_log_stack() -> None:
    compose = yaml.safe_load((DEPLOY_DIR / "compose.prod.yml").read_text(encoding="utf-8"))
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    project = tomllib.loads((ROOT / "backend" / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {
        dependency.split("[", maxsplit=1)[0].split("<", maxsplit=1)[0].split(">", maxsplit=1)[0]
        for dependency in project["project"]["dependencies"]
    }

    assert set(compose["services"]) == {
        "redis",
        "mysql",
        "qdrant",
        "elasticsearch",
        "migrate",
        "backend",
        "frontend",
    }
    assert set(compose["services"]).isdisjoint({"kibana", "celery"})
    assert {"python-socketio", "redis"}.issubset(dependencies)
    assert "celery" not in dependencies
    assert 'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]' in dockerfile
    assert "--workers" not in dockerfile


def test_nginx_routes_api_and_frontend_to_loopback_ports() -> None:
    nginx = (DEPLOY_DIR / "nginx" / "auto-reign.conf").read_text(encoding="utf-8")

    assert "server_name auto-reign.agdoer.com;" in nginx
    assert "location /api/" in nginx
    assert "proxy_pass http://127.0.0.1:18300;" in nginx
    assert "proxy_pass http://127.0.0.1:13100;" in nginx
    assert "proxy_buffering off;" in nginx


def test_nginx_routes_socketio_transport_before_frontend() -> None:
    nginx = (DEPLOY_DIR / "nginx" / "auto-reign.conf").read_text(encoding="utf-8")

    socket_location = nginx.index("location /socket.io/")
    api_location = nginx.index("location /api/")
    frontend_location = nginx.index("location / {")
    assert socket_location < api_location < frontend_location
    socket_block = nginx[socket_location:api_location]
    assert "proxy_pass http://127.0.0.1:18300;" in socket_block
    assert "proxy_http_version 1.1;" in socket_block
    assert "proxy_set_header Upgrade $http_upgrade;" in socket_block
    assert (
        "proxy_set_header Connection $auto_reign_connection_upgrade;"
        in socket_block
    )
    assert "proxy_read_timeout 600s;" in socket_block
    assert "proxy_send_timeout 600s;" in socket_block
    assert "proxy_buffering off;" in socket_block
    assert "location /chat" not in nginx


def test_ci_provisions_redis_and_runs_task_runtime_integrations() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    backend = workflow["jobs"]["backend"]

    redis = backend["services"]["redis"]
    assert redis["image"] == "redis:7.4-alpine"
    commands = "\n".join(
        step.get("run", "") for step in backend["steps"] if isinstance(step, dict)
    )
    assert "tests/integration/test_subtask_context_binding_mysql.py" in commands
    assert "tests/integration/test_task_room_mysql_redis.py" in commands
    assert "test_attachment_binding_mysql.py" not in commands


def test_deploy_prepares_redis_before_migrations() -> None:
    script = (DEPLOY_DIR / "deploy.sh").read_text(encoding="utf-8")
    commands = [
        (index, shlex.split(line.strip()))
        for index, line in enumerate(script.splitlines())
        if line.strip().startswith("compose ")
    ]
    pull_index, pull = next(
        (index, tokens)
        for index, tokens in commands
        if tokens[:2] == ["compose", "pull"]
    )
    start_index, start = next(
        (index, tokens)
        for index, tokens in commands
        if tokens[:3] == ["compose", "up", "-d"] and "mysql" in tokens
    )
    migrate_index, migrate = next(
        (index, tokens)
        for index, tokens in commands
        if tokens[:3] == ["compose", "run", "--rm"]
    )

    assert "redis" in pull[2:]
    assert "redis" in start[3:]
    assert migrate == ["compose", "run", "--rm", "migrate"]
    assert pull_index < start_index < migrate_index


def test_deploy_paths_require_redis_directory() -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            (
                'source "$1"; '
                "AUTO_REIGN_DATA_DIR=/srv/data; "
                "AUTO_REIGN_MYSQL_DIR=/srv/mysql; "
                "AUTO_REIGN_QDRANT_DIR=/srv/qdrant; "
                "AUTO_REIGN_BACKUP_DIR=/srv/backups; "
                "ENV_FILE=/tmp/production.env; "
                "unset AUTO_REIGN_REDIS_DIR; "
                "require_deploy_paths"
            ),
            "deploy-path-test",
            str(DEPLOY_DIR / "lib.sh"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": os.environ["PATH"]},
    )

    assert result.returncode != 0
    assert "Set AUTO_REIGN_REDIS_DIR in /tmp/production.env" in result.stderr


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
        assert key not in values, f"duplicate environment key: {key}"
        values[key] = value
    return values
