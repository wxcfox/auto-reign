from __future__ import annotations

import importlib.util
import signal
import socket
import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "start.py"

spec = importlib.util.spec_from_file_location("auto_reign_start", SCRIPT_PATH)
assert spec is not None
assert spec.loader is not None
start_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = start_module
spec.loader.exec_module(start_module)


def test_find_available_port_advances_past_listener() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        occupied_port = listener.getsockname()[1]

        selected_port = start_module.find_available_port(occupied_port)

    assert selected_port > occupied_port


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
