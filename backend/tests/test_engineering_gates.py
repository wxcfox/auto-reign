from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_GATE = REPOSITORY_ROOT / "scripts" / "check-alembic-heads.sh"
DOCS_GATE = REPOSITORY_ROOT / "scripts" / "check-docs-impact.sh"


def _run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _git(repository: Path, *arguments: str) -> str:
    result = _run(["git", *arguments], cwd=repository)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _write(repository: Path, relative_path: str, content: str) -> None:
    target = repository / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _commit(repository: Path, message: str) -> str:
    _git(repository, "add", ".")
    _git(
        repository,
        "-c",
        "user.name=Engineering Gate Test",
        "-c",
        "user.email=engineering-gate@example.invalid",
        "commit",
        "-m",
        message,
    )
    return _git(repository, "rev-parse", "HEAD")


@pytest.fixture
def docs_gate_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "--quiet")
    (repository / "scripts").mkdir()
    shutil.copy2(DOCS_GATE, repository / "scripts" / DOCS_GATE.name)
    _write(repository, "README.md", "baseline\n")
    _write(repository, "docs/workbench-architecture.md", "baseline\n")
    _write(repository, "docs/knowledge-data-flow.md", "baseline\n")
    _write(repository, "docs/production-deployment.md", "baseline\n")
    base = _commit(repository, "baseline")
    return repository, base


def test_docs_impact_accepts_mapped_authoritative_document(
    docs_gate_repository: tuple[Path, str],
) -> None:
    repository, base = docs_gate_repository
    _write(repository, "backend/app/services/task_service.py", "changed\n")
    _write(repository, "docs/workbench-architecture.md", "updated\n")
    _commit(repository, "update task contract")

    result = _run(
        ["bash", str(repository / "scripts" / DOCS_GATE.name), base],
        cwd=repository / "docs",
    )

    assert result.returncode == 0, result.stderr
    assert "passed" in result.stdout


def test_docs_impact_rejects_high_impact_change_without_documentation(
    docs_gate_repository: tuple[Path, str],
) -> None:
    repository, base = docs_gate_repository
    _write(repository, "backend/app/services/knowledge_index_worker.py", "changed\n")
    _commit(repository, "change knowledge indexing")

    result = _run(
        ["bash", str(repository / "scripts" / DOCS_GATE.name), base],
        cwd=repository,
    )

    assert result.returncode == 1
    assert "docs/knowledge-data-flow.md" in result.stderr


def test_docs_impact_rejects_deployment_change_without_documentation(
    docs_gate_repository: tuple[Path, str],
) -> None:
    repository, base = docs_gate_repository
    _write(repository, "deploy/compose.prod.yml", "changed\n")
    _commit(repository, "change production deployment")

    result = _run(
        ["bash", str(repository / "scripts" / DOCS_GATE.name), base],
        cwd=repository,
    )

    assert result.returncode == 1
    assert "docs/production-deployment.md" in result.stderr


def test_docs_impact_rejects_readme_as_cross_domain_substitute(
    docs_gate_repository: tuple[Path, str],
) -> None:
    repository, base = docs_gate_repository
    _write(repository, "backend/app/services/task_service.py", "changed\n")
    _write(repository, "backend/app/services/knowledge_index_worker.py", "changed\n")
    _write(repository, "README.md", "updated behavior\n")
    _commit(repository, "document cross-domain behavior")

    result = _run(
        ["bash", str(repository / "scripts" / DOCS_GATE.name), base],
        cwd=repository,
    )

    assert result.returncode == 1
    assert "docs/workbench-architecture.md" in result.stderr
    assert "docs/knowledge-data-flow.md" in result.stderr


def test_docs_impact_accepts_all_mapped_cross_domain_documents(
    docs_gate_repository: tuple[Path, str],
) -> None:
    repository, base = docs_gate_repository
    _write(repository, "backend/app/services/task_service.py", "changed\n")
    _write(repository, "backend/app/services/knowledge_index_worker.py", "changed\n")
    _write(repository, "docs/workbench-architecture.md", "updated\n")
    _write(repository, "docs/knowledge-data-flow.md", "updated\n")
    _commit(repository, "document cross-domain behavior")

    result = _run(
        ["bash", str(repository / "scripts" / DOCS_GATE.name), base],
        cwd=repository,
    )

    assert result.returncode == 0, result.stderr


def test_docs_impact_rejects_deleted_high_impact_path(
    docs_gate_repository: tuple[Path, str],
) -> None:
    repository, _ = docs_gate_repository
    _write(repository, "backend/app/services/task_service.py", "baseline\n")
    base = _commit(repository, "add task service")
    (repository / "backend/app/services/task_service.py").unlink()
    _commit(repository, "delete task service")

    result = _run(
        ["bash", str(repository / "scripts" / DOCS_GATE.name), base],
        cwd=repository,
    )

    assert result.returncode == 1
    assert "docs/workbench-architecture.md" in result.stderr


def test_docs_impact_rejects_high_impact_path_renamed_out_of_scope(
    docs_gate_repository: tuple[Path, str],
) -> None:
    repository, _ = docs_gate_repository
    original = repository / "backend/app/services/task_service.py"
    _write(repository, "backend/app/services/task_service.py", "baseline\n")
    base = _commit(repository, "add task service")
    target = repository / "legacy/task_service.py"
    target.parent.mkdir(parents=True)
    original.rename(target)
    _commit(repository, "move task service")

    result = _run(
        ["bash", str(repository / "scripts" / DOCS_GATE.name), base],
        cwd=repository,
    )

    assert result.returncode == 1
    assert "docs/workbench-architecture.md" in result.stderr


def test_docs_impact_maps_agent_and_workspace_contracts(
    docs_gate_repository: tuple[Path, str],
) -> None:
    repository, base = docs_gate_repository
    _write(repository, "backend/app/api/agents.py", "changed\n")
    _write(repository, "backend/app/services/workspace_resource_service.py", "changed\n")
    _commit(repository, "change resource contracts")

    result = _run(
        ["bash", str(repository / "scripts" / DOCS_GATE.name), base],
        cwd=repository,
    )

    assert result.returncode == 1
    assert "docs/workbench-architecture.md" in result.stderr


def test_docs_impact_accepts_unrelated_change(
    docs_gate_repository: tuple[Path, str],
) -> None:
    repository, base = docs_gate_repository
    _write(repository, "backend/app/services/model_service.py", "changed\n")
    _commit(repository, "change model adapter")

    result = _run(
        ["bash", str(repository / "scripts" / DOCS_GATE.name), base],
        cwd=repository,
    )

    assert result.returncode == 0, result.stderr


def test_docs_impact_accepts_root_baseline_with_authoritative_documents(
    docs_gate_repository: tuple[Path, str],
) -> None:
    repository, _ = docs_gate_repository
    _write(repository, "backend/app/services/task_service.py", "current\n")
    _commit(repository, "add current task service")

    result = _run(
        ["bash", str(repository / "scripts" / DOCS_GATE.name), "--root"],
        cwd=repository,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("arguments", "expected_error"),
    [
        ([], "usage:"),
        (["not-a-ref"], "base ref is not a valid commit"),
    ],
)
def test_docs_impact_rejects_missing_or_invalid_base(
    docs_gate_repository: tuple[Path, str],
    arguments: list[str],
    expected_error: str,
) -> None:
    repository, _ = docs_gate_repository

    result = _run(
        ["bash", str(repository / "scripts" / DOCS_GATE.name), *arguments],
        cwd=repository,
    )

    assert result.returncode == 2
    assert expected_error in result.stderr


@pytest.mark.parametrize(
    ("heads", "expected_returncode", "expected_message"),
    [
        ("abc123 (head)\n", 0, "Alembic head check passed"),
        ("abc123 (head)\ndef456 (head)\n", 1, "expected exactly one Alembic head, found 2"),
        ("", 1, "expected exactly one Alembic head, found 0"),
    ],
)
def test_alembic_gate_requires_exactly_one_head_from_any_working_directory(
    tmp_path: Path,
    heads: str,
    expected_returncode: int,
    expected_message: str,
) -> None:
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    backend_alembic = repository / "backend" / "alembic"
    fake_bin = tmp_path / "bin"
    arbitrary_cwd = tmp_path / "elsewhere"
    scripts.mkdir(parents=True)
    backend_alembic.mkdir(parents=True)
    fake_bin.mkdir()
    arbitrary_cwd.mkdir()
    shutil.copy2(ALEMBIC_GATE, scripts / ALEMBIC_GATE.name)
    _write(
        tmp_path,
        "bin/uv",
        "#!/usr/bin/env bash\n"
        'printf "%s" "$FAKE_ALEMBIC_HEADS"\n'
        'printf "%s\\n" "$PWD|$*" > "$FAKE_UV_LOG"\n',
    )
    (fake_bin / "uv").chmod(0o755)
    invocation_log = tmp_path / "uv-invocation.log"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_ALEMBIC_HEADS": heads,
        "FAKE_UV_LOG": str(invocation_log),
    }

    result = _run(
        ["bash", str(scripts / ALEMBIC_GATE.name)],
        cwd=arbitrary_cwd,
        env=env,
    )

    combined_output = result.stdout + result.stderr
    assert result.returncode == expected_returncode
    assert expected_message in combined_output
    assert invocation_log.read_text(encoding="utf-8").strip() == (
        f"{repository / 'backend'}|run alembic heads"
    )


def test_alembic_gate_reports_missing_uv(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    (repository / "backend" / "alembic").mkdir(parents=True)
    scripts.mkdir()
    shutil.copy2(ALEMBIC_GATE, scripts / ALEMBIC_GATE.name)
    env = {**os.environ, "PATH": "/usr/bin:/bin"}

    result = _run(
        ["bash", str(scripts / ALEMBIC_GATE.name)],
        cwd=repository,
        env=env,
    )

    assert result.returncode == 2
    assert "uv is required" in result.stderr


def test_alembic_gate_reports_uv_failure(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    fake_bin = tmp_path / "bin"
    (repository / "backend" / "alembic").mkdir(parents=True)
    scripts.mkdir()
    fake_bin.mkdir()
    shutil.copy2(ALEMBIC_GATE, scripts / ALEMBIC_GATE.name)
    _write(
        tmp_path,
        "bin/uv",
        "#!/usr/bin/env bash\n"
        'echo "simulated alembic failure" >&2\n'
        "exit 9\n",
    )
    (fake_bin / "uv").chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }

    result = _run(
        ["bash", str(scripts / ALEMBIC_GATE.name)],
        cwd=repository,
        env=env,
    )

    assert result.returncode == 2
    assert "failed to inspect Alembic heads" in result.stderr
    assert "simulated alembic failure" in result.stderr
