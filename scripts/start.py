from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path

PROJECT_NAME = "auto-reign"
MYSQL_START_TIMEOUT = 60.0
QDRANT_START_TIMEOUT = 60.0
APP_START_TIMEOUT = 90.0
STOP_TIMEOUT = 5.0


@dataclass(frozen=True)
class ServiceState:
    pid: int
    port: int
    marker: str


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    pid_dir: Path
    log_dir: Path


def load_env(path: Path, environ: MutableMapping[str, str]) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        environ.setdefault(key.strip(), value)


def find_available_port(start_port: int) -> int:
    for port in range(start_port, 65536):
        with socket.socket() as candidate:
            candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                candidate.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"No available TCP port at or above {start_port}")


def read_state(path: Path) -> ServiceState | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ServiceState(
            pid=int(payload["pid"]),
            port=int(payload["port"]),
            marker=str(payload["marker"]),
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return None


def read_process_command(pid: int) -> str | None:
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def read_process_cwd(pid: int) -> Path | None:
    result = subprocess.run(
        ["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("n") and len(line) > 1:
            return Path(line[1:])
    return None


def listener_pid_for_port(port: int) -> int | None:
    result = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            return int(line[1:])
    return None


def expected_cwd_for_state(state: ServiceState) -> Path | None:
    root = Path(__file__).resolve().parents[1]
    if state.marker.startswith("auto-reign-backend"):
        return root / "backend"
    if state.marker.startswith("auto-reign-frontend"):
        return root / "frontend"
    return None


def state_matches_process(
    state: ServiceState,
    command_for_pid: Callable[[int], str | None] | None = None,
    cwd_for_pid: Callable[[int], Path | None] | None = None,
    listener_pid_for_port_fn: Callable[[int], int | None] | None = None,
) -> bool:
    inspect_command = command_for_pid or read_process_command
    process_cwd = cwd_for_pid or read_process_cwd
    listener_for_port = listener_pid_for_port_fn or listener_pid_for_port
    command = inspect_command(state.pid)
    if command is not None and state.marker in command:
        return True
    expected_cwd = expected_cwd_for_state(state)
    if expected_cwd is None:
        return False
    if listener_for_port(state.port) != state.pid:
        return False
    cwd = process_cwd(state.pid)
    return cwd == expected_cwd


def process_matches(state: ServiceState) -> bool:
    return state_matches_process(state)


def stop_managed_process(
    state_path: Path,
    command_for_pid: Callable[[int], str | None] | None = None,
    signal_group: Callable[[int, int], None] | None = None,
    cwd_for_pid: Callable[[int], Path | None] | None = None,
    listener_pid_for_port_fn: Callable[[int], int | None] | None = None,
) -> bool:
    if signal_group is None:
        def send_signal(pid: int, sig: int) -> None:
            os.killpg(os.getpgid(pid), sig)
    else:
        send_signal = signal_group
    state = read_state(state_path)
    if state is None:
        return False
    if not state_matches_process(
        state,
        command_for_pid=command_for_pid,
        cwd_for_pid=cwd_for_pid,
        listener_pid_for_port_fn=listener_pid_for_port_fn,
    ):
        state_path.unlink(missing_ok=True)
        return False
    send_signal(state.pid, signal.SIGTERM)
    state_path.unlink(missing_ok=True)
    return True


def healthy_managed_state(
    state_path: Path,
    health_url_for: Callable[[ServiceState], str],
    command_for_pid: Callable[[int], str | None] | None = None,
    http_probe: Callable[[str, float], bool] | None = None,
    cwd_for_pid: Callable[[int], Path | None] | None = None,
    listener_pid_for_port_fn: Callable[[int], int | None] | None = None,
) -> ServiceState | None:
    probe = http_probe or wait_for_http
    state = read_state(state_path)
    if state is None:
        return None
    if not state_matches_process(
        state,
        command_for_pid=command_for_pid,
        cwd_for_pid=cwd_for_pid,
        listener_pid_for_port_fn=listener_pid_for_port_fn,
    ):
        state_path.unlink(missing_ok=True)
        return None
    return state if probe(health_url_for(state), 2) else None


def wait_for_http(url: str, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    return False


def wait_for_tcp(host: str, port: int, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def compose_service_health(
    root: Path,
    service: str,
    env: MutableMapping[str, str] | None = None,
) -> str | None:
    container_id_result = subprocess.run(
        ["docker", "compose", "-p", PROJECT_NAME, "ps", "-q", service],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    container_id = container_id_result.stdout.strip()
    if container_id_result.returncode != 0 or not container_id:
        return None
    inspect_result = subprocess.run(
        [
            "docker",
            "inspect",
            container_id,
            "--format",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if inspect_result.returncode != 0:
        return None
    return inspect_result.stdout.strip() or None


def wait_for_compose_service_health(
    root: Path,
    service: str,
    timeout_seconds: float,
    env: MutableMapping[str, str] | None = None,
    health_reader: Callable[[Path, str, MutableMapping[str, str] | None], str | None] | None = None,
) -> bool:
    read_health = health_reader or compose_service_health
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if read_health(root, service, env) == "healthy":
            return True
        time.sleep(0.5)
    return False


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage the Auto Reign development stack.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="Show current service status.")
    group.add_argument("--stop", action="store_true", help="Stop managed services.")
    group.add_argument("--restart", action="store_true", help="Restart managed services.")
    return parser.parse_args(argv)


def runtime_paths(root: Path) -> RuntimePaths:
    return RuntimePaths(root=root, pid_dir=root / ".pids", log_dir=root / "logs")


def ensure_runtime_dirs(paths: RuntimePaths) -> None:
    paths.pid_dir.mkdir(parents=True, exist_ok=True)
    paths.log_dir.mkdir(parents=True, exist_ok=True)


def ensure_env_file(root: Path) -> Path:
    env_path = root / ".env"
    if not env_path.exists():
        shutil.copyfile(root / ".env.example", env_path)
    return env_path


def load_runtime_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    load_env(ensure_env_file(root), env)
    return env


def require_commands(commands: Sequence[str]) -> None:
    missing = [command for command in commands if shutil.which(command) is None]
    if missing:
        raise RuntimeError(f"Missing required command(s): {', '.join(missing)}")


def run_command(
    args: Sequence[str],
    *,
    cwd: Path,
    env: MutableMapping[str, str] | None = None,
) -> None:
    result = subprocess.run(args, cwd=cwd, env=env, check=False)
    if result.returncode != 0:
        hint = ""
        if list(args[:6]) == ["docker", "compose", "-p", PROJECT_NAME, "up", "-d"]:
            mysql_image = (env or {}).get("MYSQL_IMAGE", "mysql:8.4")
            qdrant_image = (env or {}).get("QDRANT_IMAGE", "qdrant/qdrant:v1.17.0")
            hint = (
                " If Docker Hub access is unstable, set MYSQL_IMAGE and QDRANT_IMAGE in .env "
                f"to reachable mirror images, then retry. Current values: MYSQL_IMAGE={mysql_image}, "
                f"QDRANT_IMAGE={qdrant_image}."
            )
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(args)}{hint}")


def backend_state_path(paths: RuntimePaths) -> Path:
    return paths.pid_dir / "backend.json"


def frontend_state_path(paths: RuntimePaths) -> Path:
    return paths.pid_dir / "frontend.json"


def backend_log_path(paths: RuntimePaths) -> Path:
    return paths.log_dir / "backend.log"


def frontend_log_path(paths: RuntimePaths) -> Path:
    return paths.log_dir / "frontend.log"


def write_state(path: Path, state: ServiceState) -> None:
    payload = {"pid": state.pid, "port": state.port, "marker": state.marker}
    path.write_text(json.dumps(payload), encoding="utf-8")


def backend_health_url(state: ServiceState) -> str:
    return f"http://127.0.0.1:{state.port}/api/health"


def frontend_health_url(state: ServiceState) -> str:
    return f"http://127.0.0.1:{state.port}/"


def parse_int(env: MutableMapping[str, str], name: str, default: int) -> int:
    raw_value = env.get(name, str(default))
    try:
        return int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw_value!r}") from exc


def database_endpoint(database_url: str) -> tuple[str, int]:
    parsed = urllib.parse.urlparse(database_url)
    if not parsed.hostname:
        raise RuntimeError("DATABASE_URL must include a hostname")
    return parsed.hostname, parsed.port or 3306


def qdrant_ready_url(qdrant_url: str) -> str:
    return urllib.parse.urljoin(qdrant_url.rstrip("/") + "/", "readyz")


def service_marker(name: str) -> str:
    return f"auto-reign-{name}"


def verify_docker() -> None:
    result = subprocess.run(
        ["docker", "info"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "docker info failed"
        raise RuntimeError(f"Docker is not ready: {message}")


def start_dependency_containers(root: Path, env: MutableMapping[str, str]) -> None:
    run_command(
        ["docker", "compose", "-p", PROJECT_NAME, "up", "-d", "mysql", "qdrant"],
        cwd=root,
        env=env,
    )


def stop_dependency_containers(root: Path, env: MutableMapping[str, str]) -> None:
    run_command(
        ["docker", "compose", "-p", PROJECT_NAME, "stop", "mysql", "qdrant"],
        cwd=root,
        env=env,
    )


def wait_for_dependencies(root: Path, env: MutableMapping[str, str]) -> None:
    database_url = env.get("DATABASE_URL", "")
    qdrant_url = env.get("QDRANT_URL", "")
    mysql_host, mysql_port = database_endpoint(database_url)
    if not wait_for_tcp(mysql_host, mysql_port, MYSQL_START_TIMEOUT):
        raise RuntimeError(f"MySQL did not become reachable at {mysql_host}:{mysql_port}")
    if not wait_for_compose_service_health(root, "mysql", MYSQL_START_TIMEOUT, env):
        raise RuntimeError("MySQL did not report healthy status")
    ready_url = qdrant_ready_url(qdrant_url)
    if not wait_for_http(ready_url, QDRANT_START_TIMEOUT):
        raise RuntimeError(f"Qdrant did not become ready at {ready_url}")


def backend_env(root: Path, env: MutableMapping[str, str], port: int) -> dict[str, str]:
    result = dict(env)
    result["BACKEND_HOST"] = "127.0.0.1"
    result["BACKEND_PORT"] = str(port)
    data_dir = root / result.get("DATA_DIR", "./data")
    result["DATA_DIR"] = str(data_dir.resolve())
    return result


def frontend_env(env: MutableMapping[str, str], backend_port: int) -> dict[str, str]:
    result = dict(env)
    result["NEXT_PUBLIC_API_BASE_URL"] = f"http://127.0.0.1:{backend_port}"
    return result


def stop_managed_process_with_timeout(
    state_path: Path,
    command_for_pid: Callable[[int], str | None] | None = None,
    signal_group: Callable[[int, int], None] | None = None,
) -> bool:
    inspect_command = command_for_pid or read_process_command
    if signal_group is None:
        def send_signal(pid: int, sig: int) -> None:
            os.killpg(os.getpgid(pid), sig)
    else:
        send_signal = signal_group
    state = read_state(state_path)
    if state is None:
        return False
    stopped = stop_managed_process(
        state_path,
        command_for_pid=inspect_command,
        signal_group=send_signal,
    )
    if not stopped:
        return False
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline:
        command = inspect_command(state.pid)
        if command is None or state.marker not in command:
            return True
        time.sleep(0.2)
    command = inspect_command(state.pid)
    if command is not None and state.marker in command:
        send_signal(state.pid, signal.SIGKILL)
    return True


def prepare_backend(root: Path, env: MutableMapping[str, str]) -> None:
    backend_dir = root / "backend"
    run_command(["uv", "sync"], cwd=backend_dir, env=env)
    run_command(["uv", "run", "alembic", "upgrade", "head"], cwd=backend_dir, env=env)


def prepare_frontend(root: Path, env: MutableMapping[str, str]) -> None:
    frontend_dir = root / "frontend"
    if not (frontend_dir / "node_modules").exists():
        run_command(["npm", "install"], cwd=frontend_dir, env=env)


def launch_managed_process(
    *,
    command: Sequence[str],
    cwd: Path,
    env: MutableMapping[str, str],
    log_path: Path,
    marker: str,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    shell_command = f"AUTO_REIGN_MARKER={shlex.quote(marker)} {shlex.join(command)}"
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            ["/bin/sh", "-lc", shell_command],
            cwd=cwd,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return process.pid


def ensure_backend(paths: RuntimePaths, env: MutableMapping[str, str]) -> ServiceState:
    state = healthy_managed_state(backend_state_path(paths), backend_health_url)
    if state is not None:
        return state

    state_path = backend_state_path(paths)
    stop_managed_process_with_timeout(state_path)
    port = find_available_port(parse_int(env, "BACKEND_PORT", 8300))
    marker = service_marker("backend")
    process_env = backend_env(paths.root, env, port)
    pid = launch_managed_process(
        command=[
            "uv",
            "run",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=paths.root / "backend",
        env=process_env,
        log_path=backend_log_path(paths),
        marker=marker,
    )
    state = ServiceState(pid=pid, port=port, marker=marker)
    write_state(state_path, state)
    if not wait_for_http(backend_health_url(state), APP_START_TIMEOUT):
        stop_managed_process_with_timeout(state_path)
        raise RuntimeError(f"Backend failed to start. Check {backend_log_path(paths)}")
    listener_pid = listener_pid_for_port(port)
    if listener_pid is not None:
        state = ServiceState(pid=listener_pid, port=port, marker=marker)
        write_state(state_path, state)
    return state


def ensure_frontend(
    paths: RuntimePaths,
    env: MutableMapping[str, str],
    backend: ServiceState,
) -> ServiceState:
    state = healthy_managed_state(frontend_state_path(paths), frontend_health_url)
    if state is not None:
        return state

    state_path = frontend_state_path(paths)
    stop_managed_process_with_timeout(state_path)
    port = find_available_port(parse_int(env, "FRONTEND_PORT", 3100))
    marker = f"{service_marker('frontend')}-backend-{backend.port}"
    process_env = frontend_env(env, backend.port)
    pid = launch_managed_process(
        command=[
            "npm",
            "run",
            "dev",
            "--",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=paths.root / "frontend",
        env=process_env,
        log_path=frontend_log_path(paths),
        marker=marker,
    )
    state = ServiceState(pid=pid, port=port, marker=marker)
    write_state(state_path, state)
    if not wait_for_http(frontend_health_url(state), APP_START_TIMEOUT):
        stop_managed_process_with_timeout(state_path)
        raise RuntimeError(f"Frontend failed to start. Check {frontend_log_path(paths)}")
    listener_pid = listener_pid_for_port(port)
    if listener_pid is not None:
        state = ServiceState(pid=listener_pid, port=port, marker=marker)
        write_state(state_path, state)
    return state


def format_host_status(name: str, state: ServiceState | None, url_for: Callable[[ServiceState], str]) -> str:
    if state is None:
        return f"{name}: stopped"
    healthy = wait_for_http(url_for(state), 1.0)
    status = "healthy" if healthy else "unhealthy"
    return f"{name}: {status} pid={state.pid} port={state.port}"


def format_dependency_status(name: str, healthy: bool, target: str) -> str:
    status = "healthy" if healthy else "stopped"
    return f"{name}: {status} target={target}"


def show_status(paths: RuntimePaths, env: MutableMapping[str, str]) -> int:
    database_url = env.get("DATABASE_URL", "")
    qdrant_url = env.get("QDRANT_URL", "")
    mysql_host, mysql_port = database_endpoint(database_url)
    mysql_healthy = wait_for_tcp(mysql_host, mysql_port, 1.0)
    qdrant_target = qdrant_ready_url(qdrant_url)
    qdrant_healthy = wait_for_http(qdrant_target, 1.0)
    backend = healthy_managed_state(backend_state_path(paths), backend_health_url)
    frontend = healthy_managed_state(frontend_state_path(paths), frontend_health_url)
    print(format_dependency_status("mysql", mysql_healthy, f"{mysql_host}:{mysql_port}"))
    print(format_dependency_status("qdrant", qdrant_healthy, qdrant_target))
    print(format_host_status("backend", backend, backend_health_url))
    print(format_host_status("frontend", frontend, frontend_health_url))
    return 0


def start_stack(paths: RuntimePaths, env: MutableMapping[str, str]) -> int:
    ensure_runtime_dirs(paths)
    require_commands(["docker", "uv", "node", "npm"])
    verify_docker()
    start_dependency_containers(paths.root, env)
    wait_for_dependencies(paths.root, env)
    prepare_backend(paths.root, env)
    prepare_frontend(paths.root, env)
    started_paths: list[Path] = []
    try:
        previous_backend = read_state(backend_state_path(paths))
        backend = ensure_backend(paths, env)
        if previous_backend is None or previous_backend.pid != backend.pid:
            started_paths.append(backend_state_path(paths))
        previous_frontend = read_state(frontend_state_path(paths))
        frontend = ensure_frontend(paths, env, backend)
        if previous_frontend is None or previous_frontend.pid != frontend.pid:
            started_paths.append(frontend_state_path(paths))
    except Exception:
        for state_path in reversed(started_paths):
            stop_managed_process_with_timeout(state_path)
        raise
    print(f"MySQL: ready on {database_endpoint(env['DATABASE_URL'])[0]}:{database_endpoint(env['DATABASE_URL'])[1]}")
    print(f"Qdrant: ready on {qdrant_ready_url(env['QDRANT_URL'])}")
    print(f"Backend:  http://127.0.0.1:{backend.port}")
    print(f"Frontend: http://127.0.0.1:{frontend.port}")
    return 0


def stop_stack(paths: RuntimePaths, env: MutableMapping[str, str]) -> int:
    ensure_runtime_dirs(paths)
    stop_managed_process_with_timeout(frontend_state_path(paths))
    stop_managed_process_with_timeout(backend_state_path(paths))
    stop_dependency_containers(paths.root, env)
    return show_status(paths, env)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parents[1]
    env = load_runtime_env(root)
    paths = runtime_paths(root)
    if args.status:
        return show_status(paths, env)
    if args.stop:
        return stop_stack(paths, env)
    if args.restart:
        stop_stack(paths, env)
        return start_stack(paths, env)
    return start_stack(paths, env)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
