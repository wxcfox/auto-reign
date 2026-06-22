from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

PROJECT_NAME = "auto-reign"
BASELINE_RUNTIME_PATHS = ("data", "backend/data", ".pids", "logs")
ENV_PATH_KEYS = ("DATA_DIR", "SQLITE_PATH", "CHROMA_DIR")


@dataclass(frozen=True)
class ResetResult:
    removed_paths: list[Path]
    skipped_paths: list[Path]


def reset_all_data(
    *,
    root: Path,
    yes: bool,
    dry_run: bool,
    skip_docker: bool,
    command_runner: Callable[[list[str]], None] | None = None,
) -> ResetResult:
    root = root.resolve()
    if not yes and not dry_run:
        raise SystemExit("Refusing to reset data without --yes.")

    runner = command_runner or _run_command
    if not skip_docker:
        _run_or_print(["./start.sh", "--stop"], root=root, dry_run=dry_run, runner=runner)
        _run_or_print(
            ["docker", "compose", "-p", PROJECT_NAME, "down", "-v", "--remove-orphans"],
            root=root,
            dry_run=dry_run,
            runner=runner,
        )

    removed_paths: list[Path] = []
    skipped_paths: list[Path] = []
    for path in _candidate_paths(root):
        if not path.exists():
            skipped_paths.append(path)
            continue
        if dry_run:
            removed_paths.append(path)
            continue
        _remove_path(path)
        removed_paths.append(path)

    return ResetResult(removed_paths=removed_paths, skipped_paths=skipped_paths)


def _run_or_print(
    command: list[str],
    *,
    root: Path,
    dry_run: bool,
    runner: Callable[[list[str]], None],
) -> None:
    if dry_run:
        print("$ " + " ".join(command))
        return
    runner(command)


def _run_command(command: list[str]) -> None:
    check = command[:2] != ["./start.sh", "--stop"]
    subprocess.run(command, cwd=_repo_root(), check=check)


def _candidate_paths(root: Path) -> list[Path]:
    paths = {_safe_repo_path(root, relative) for relative in BASELINE_RUNTIME_PATHS}
    env = _load_env(root / ".env.example")
    env.update(_load_env(root / ".env"))
    for key in ENV_PATH_KEYS:
        value = env.get(key, "").strip()
        if value:
            paths.add(_safe_repo_path(root, value))
    paths.update(root.glob("data.backup-*"))
    return sorted(paths)


def _safe_repo_path(root: Path, value: str) -> Path:
    raw_path = Path(value).expanduser()
    path = raw_path if raw_path.is_absolute() else root / raw_path
    resolved = path.resolve(strict=False)
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError(f"Refusing to remove path outside repository: {path}")
    return resolved


def _load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Remove all local Auto Reign runtime data and Docker volumes."
    )
    parser.add_argument("--yes", action="store_true", help="Actually remove data.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be removed.")
    parser.add_argument(
        "--skip-docker",
        action="store_true",
        help="Only remove filesystem runtime data; do not stop services or remove Docker volumes.",
    )
    args = parser.parse_args(argv)

    try:
        result = reset_all_data(
            root=_repo_root(),
            yes=args.yes,
            dry_run=args.dry_run,
            skip_docker=args.skip_docker,
        )
    except (SystemExit, ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    verb = "Would remove" if args.dry_run else "Removed"
    for path in result.removed_paths:
        print(f"{verb}: {path.relative_to(_repo_root())}")
    if not result.removed_paths:
        print("No local filesystem runtime data found.")
    if not args.skip_docker and args.dry_run:
        print("Docker MySQL and Qdrant volumes would be reset.")
    elif not args.skip_docker:
        print("Docker MySQL and Qdrant volumes were reset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
