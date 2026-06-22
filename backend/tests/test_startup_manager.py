from __future__ import annotations

import importlib.util
import signal
import socket
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "start.py"

spec = importlib.util.spec_from_file_location("auto_reign_start", SCRIPT_PATH)
assert spec is not None
assert spec.loader is not None
start_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = start_module
spec.loader.exec_module(start_module)


def test_require_configured_port_accepts_free_port() -> None:
    with socket.socket() as candidate:
        candidate.bind(("127.0.0.1", 0))
        free_port = candidate.getsockname()[1]

    selected_port = start_module.require_configured_port(
        free_port,
        "backend",
        listener_pid_for_port_fn=lambda port: None,
    )

    assert selected_port == free_port


def test_require_configured_port_rejects_other_worktree_listener() -> None:
    other_worktree = Path("/repo/.worktrees/feature/backend")

    try:
        start_module.require_configured_port(
            8300,
            "backend",
            listener_pid_for_port_fn=lambda port: 42,
            command_for_pid=lambda pid: "/usr/bin/python uvicorn app.main:app",
            cwd_for_pid=lambda pid: other_worktree,
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected strict port failure")

    assert "backend port 8300 is already in use by pid 42" in message
    assert str(other_worktree) in message
    assert "./start.sh --stop" in message


def test_require_no_checkout_service_listener_rejects_stale_same_checkout_process() -> None:
    service_cwd = Path("/repo/frontend")

    try:
        start_module.require_no_checkout_service_listener(
            service_cwd,
            "frontend",
            listener_pids_fn=lambda: [11, 22],
            cwd_for_pid=lambda pid: service_cwd if pid == 22 else Path("/repo/.worktrees/old/frontend"),
            command_for_pid=lambda pid: "next dev --port 3101",
        )
    except RuntimeError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected stale checkout process failure")

    assert "frontend is already running from this checkout as pid 22" in message
    assert str(service_cwd) in message
    assert "kill 22" in message


def test_require_no_checkout_service_listener_ignores_other_checkout_processes() -> None:
    start_module.require_no_checkout_service_listener(
        Path("/repo/frontend"),
        "frontend",
        listener_pids_fn=lambda: [11],
        cwd_for_pid=lambda pid: Path("/repo/.worktrees/old/frontend"),
        command_for_pid=lambda pid: "next dev --port 3101",
    )


def test_load_env_does_not_override_exported_value(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("BACKEND_PORT=8300\nMYSQL_PASSWORD=file-secret\n")
    environ = {"MYSQL_PASSWORD": "exported-secret"}

    start_module.load_env(env_file, environ)

    assert environ["BACKEND_PORT"] == "8300"
    assert environ["MYSQL_PASSWORD"] == "exported-secret"


def test_invalid_option_returns_usage_error() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--unknown"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "usage:" in result.stderr.lower()


def test_stale_pid_state_is_removed_without_signal(tmp_path) -> None:
    state_path = tmp_path / "backend.json"
    state_path.write_text('{"pid": 42, "port": 8300, "marker": "expected"}')
    signals: list[tuple[int, int]] = []

    stopped = start_module.stop_managed_process(
        state_path,
        command_for_pid=lambda pid: "different command",
        signal_group=lambda pid, sig: signals.append((pid, sig)),
    )

    assert stopped is False
    assert signals == []
    assert not state_path.exists()


def test_stop_signals_only_matching_process_group(tmp_path) -> None:
    state_path = tmp_path / "backend.json"
    state_path.write_text('{"pid": 42, "port": 8300, "marker": "unique-marker"}')
    signals: list[tuple[int, int]] = []

    stopped = start_module.stop_managed_process(
        state_path,
        command_for_pid=lambda pid: "python unique-marker",
        signal_group=lambda pid, sig: signals.append((pid, sig)),
    )

    assert stopped is True
    assert signals == [(42, signal.SIGTERM)]
    assert not state_path.exists()


def test_stop_waits_for_listener_port_to_release_when_command_lacks_marker(tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "backend.json"
    state_path.write_text('{"pid": 42, "port": 8300, "marker": "auto-reign-backend"}')
    signals: list[tuple[int, int]] = []
    sleeps: list[float] = []
    commands = iter(
        [
            "python auto-reign-backend",
            "python uvicorn app.main:app --port 8300",
            "python uvicorn app.main:app --port 8300",
            "python uvicorn app.main:app --port 8300",
        ]
    )
    listeners = iter([42, 42, None])

    monkeypatch.setattr(start_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(
        start_module,
        "listener_pid_for_port",
        lambda port: next(listeners),
    )

    stopped = start_module.stop_managed_process_with_timeout(
        state_path,
        command_for_pid=lambda pid: next(commands),
        signal_group=lambda pid, sig: signals.append((pid, sig)),
    )

    assert stopped is True
    assert signals == [(42, signal.SIGTERM)]
    assert sleeps == [0.2, 0.2]
    assert not state_path.exists()


def test_healthy_managed_process_is_reused(tmp_path) -> None:
    state_path = tmp_path / "frontend.json"
    state_path.write_text('{"pid": 84, "port": 3100, "marker": "next-marker"}')

    state = start_module.healthy_managed_state(
        state_path,
        health_url_for=lambda item: f"http://127.0.0.1:{item.port}/",
        command_for_pid=lambda pid: "npm next-marker",
        http_probe=lambda url, timeout: True,
    )

    assert state == start_module.ServiceState(pid=84, port=3100, marker="next-marker")


def test_wait_for_compose_service_health_retries_until_healthy(tmp_path, monkeypatch) -> None:
    checks = iter(["starting", "starting", "healthy"])

    def fake_health(_root, _service, _env):
        return next(checks)

    monkeypatch.setattr(start_module.time, "sleep", lambda _seconds: None)

    assert (
        start_module.wait_for_compose_service_health(
            tmp_path,
            "mysql",
            2,
            {},
            health_reader=fake_health,
        )
        is True
    )


def test_start_stack_checks_ports_before_dependencies_and_migrations(tmp_path, monkeypatch) -> None:
    paths = start_module.RuntimePaths(root=tmp_path, pid_dir=tmp_path / ".pids", log_dir=tmp_path / "logs")
    env = {
        "DATABASE_URL": "mysql+pymysql://auto_reign:auto_reign@127.0.0.1:13306/auto_reign",
        "QDRANT_URL": "http://127.0.0.1:16333",
        "BACKEND_PORT": "8300",
        "FRONTEND_PORT": "3100",
    }
    side_effects: list[str] = []

    monkeypatch.setattr(start_module, "require_commands", lambda commands: None)
    monkeypatch.setattr(start_module, "verify_docker", lambda: None)
    monkeypatch.setattr(
        start_module,
        "start_dependency_containers",
        lambda root, runtime_env: side_effects.append("docker"),
    )
    monkeypatch.setattr(
        start_module,
        "wait_for_dependencies",
        lambda root, runtime_env: side_effects.append("wait"),
    )
    monkeypatch.setattr(
        start_module,
        "prepare_backend",
        lambda root, runtime_env: side_effects.append("alembic"),
    )
    monkeypatch.setattr(
        start_module,
        "prepare_frontend",
        lambda root, runtime_env: side_effects.append("npm"),
    )
    monkeypatch.setattr(start_module, "require_no_checkout_service_listener", lambda service_cwd, service_name: None)

    def reject_backend_port(port: int, service_name: str) -> int:
        if service_name == "backend":
            raise RuntimeError("backend port 8300 is already in use")
        return port

    monkeypatch.setattr(start_module, "require_configured_port", reject_backend_port)

    with pytest.raises(RuntimeError, match="backend port 8300"):
        start_module.start_stack(paths, env)

    assert side_effects == []


def test_healthy_managed_state_accepts_listener_owned_by_expected_project_dir(tmp_path) -> None:
    state_path = tmp_path / "backend.json"
    state_path.write_text('{"pid": 301, "port": 8300, "marker": "auto-reign-backend"}')

    state = start_module.healthy_managed_state(
        state_path,
        health_url_for=lambda item: f"http://127.0.0.1:{item.port}/api/health",
        command_for_pid=lambda pid: "/usr/bin/python3 uvicorn app.main:app --port 8300",
        http_probe=lambda url, timeout: True,
        cwd_for_pid=lambda pid: start_module.Path(start_module.__file__).resolve().parents[1] / "backend",
        listener_pid_for_port_fn=lambda port: 301,
    )

    assert state == start_module.ServiceState(pid=301, port=8300, marker="auto-reign-backend")
