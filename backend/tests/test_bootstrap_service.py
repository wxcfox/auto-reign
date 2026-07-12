from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock, get_ident

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.init_data import (
    MAX_SEED_FILE_BYTES,
    MAX_SEED_RESOURCES,
    MAX_YAML_DEPTH,
    load_seed_resources,
)
from app.core.passwords import verify_password
from app.db.models import Base, Resource, User
from app.db.session import make_session_factory, session_scope
from app.repositories.user_repository import UserRepository
from app.schemas.agents import AgentConfig
from app.schemas.workspaces import WorkspaceConfig
from app.services.bootstrap_service import BootstrapService, bootstrap_application


WORKSPACES_YAML = """\
resources:
  - key: growth-home
    name: 成长助手工作区
    config:
      workspace_type: agent_home
      initial_agents_md: |-
        # 成长助手工作区

        本目录保存用户明确要求长期沉淀的个人学习、练习与成长资料。
"""

AGENTS_YAML = """\
resources:
  - name: 成长助手
    config:
      system_prompt: 你是用户的通用成长助手。
      default_model: null
      home_workspace_key: growth-home
      knowledge_scopes: []
"""

SECRET_MARKER = "DO_NOT_ECHO_THIS_SEED_SECRET"


class _RaceUserRepository(UserRepository):
    def __init__(self) -> None:
        self.initial_lookup_barrier = Barrier(2)
        self.winner_finished = Event()
        self.lock = Lock()
        self.initial_lookup_sessions: list[Session] = []
        self.verification_sessions: list[Session] = []
        self.create_calls = 0
        self.winner_thread_id: int | None = None

    def get_by_username(self, session: Session, username: str) -> User | None:
        user = super().get_by_username(session, username)
        if user is None:
            with self.lock:
                self.initial_lookup_sessions.append(session)
            self.initial_lookup_barrier.wait(timeout=10)
        else:
            with self.lock:
                self.verification_sessions.append(session)
        return user

    def create(self, session: Session, **values: object) -> User:
        thread_id = get_ident()
        with self.lock:
            self.create_calls += 1
            if self.winner_thread_id is None:
                self.winner_thread_id = thread_id
            is_winner = self.winner_thread_id == thread_id
        if not is_winner:
            assert self.winner_finished.wait(timeout=10)
        return super().create(session, **values)

    def mark_winner_finished(self) -> None:
        if self.winner_thread_id == get_ident():
            self.winner_finished.set()


class _CountingUserRepository(UserRepository):
    def __init__(self) -> None:
        self.create_calls = 0

    def create(self, session: Session, **values: object) -> User:
        self.create_calls += 1
        return super().create(session, **values)


class _VerificationFailureUserRepository(UserRepository):
    def __init__(self) -> None:
        self.bootstrap_error = IntegrityError(
            "insert fixed admin",
            {},
            RuntimeError("deterministic bootstrap conflict"),
        )
        self.verification_error = RuntimeError("verification database unavailable")
        self.lookup_sessions: list[Session] = []

    def get_by_username(self, session: Session, username: str) -> User | None:
        self.lookup_sessions.append(session)
        if len(self.lookup_sessions) == 1:
            return None
        raise self.verification_error

    def create(self, session: Session, **values: object) -> User:
        raise self.bootstrap_error


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bootstrap.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def init_data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "init_data"
    directory.mkdir()
    _write_seed_files(directory)
    return directory


@pytest.fixture
def invalid_init_data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "invalid_init_data"
    directory.mkdir()
    _write_seed_files(
        directory,
        agents_yaml=AGENTS_YAML.replace(
            "home_workspace_key: growth-home",
            "home_workspace_key: missing-workspace",
        ),
    )
    return directory


def _write_seed_files(
    directory: Path,
    *,
    workspaces_yaml: str = WORKSPACES_YAML,
    agents_yaml: str = AGENTS_YAML,
) -> None:
    (directory / "workspaces.yaml").write_text(workspaces_yaml, encoding="utf-8")
    (directory / "agents.yaml").write_text(agents_yaml, encoding="utf-8")


def _assert_database_is_empty(session_factory: sessionmaker[Session]) -> None:
    with session_scope(session_factory) as session:
        assert session.scalar(select(func.count(User.id))) == 0
        assert session.scalar(select(func.count(Resource.id))) == 0


def test_bootstrap_creates_admin_and_seed_resources_once(
    session_factory: sessionmaker[Session], init_data_dir: Path
) -> None:
    bootstrap_application(session_factory, init_data_dir=init_data_dir)
    with session_scope(session_factory) as session:
        admin = session.scalar(select(User).where(User.username == "admin"))
        assert admin is not None
        assert admin.role == "admin"
        assert admin.credential_bootstrap_status == "pending"
        assert admin.seed_initialized_at is not None
        assert verify_password("admin", admin.password_hash) is False
        seeded = list(session.scalars(select(Resource).where(Resource.user_id == 0)))
        assert {(item.resource_type, item.name) for item in seeded} == {
            ("agent", "成长助手"),
            ("workspace", "成长助手工作区"),
        }
        workspace = next(item for item in seeded if item.resource_type == "workspace")
        agent = next(item for item in seeded if item.resource_type == "agent")
        assert agent.config_json["home_workspace_id"] == workspace.id
        assert WorkspaceConfig.model_validate(workspace.config_json)
        assert AgentConfig.model_validate(agent.config_json)
        first_ids = {item.id for item in seeded}
        workspace.config_json = {"preserved": True}

    bootstrap_application(session_factory, init_data_dir=init_data_dir)
    with session_scope(session_factory) as session:
        assert {item.id for item in session.scalars(select(Resource))} == first_ids
        workspace = session.scalar(
            select(Resource).where(Resource.resource_type == "workspace")
        )
        assert workspace is not None
        assert workspace.config_json == {"preserved": True}


def test_invalid_seed_rolls_back_admin_and_resources(
    session_factory: sessionmaker[Session], invalid_init_data_dir: Path
) -> None:
    with pytest.raises((ValidationError, ValueError)):
        bootstrap_application(session_factory, init_data_dir=invalid_init_data_dir)
    with session_scope(session_factory) as session:
        assert session.scalar(select(func.count(User.id))) == 0
        assert session.scalar(select(func.count(Resource.id))) == 0


def test_concurrent_bootstrap_produces_one_admin_and_one_seed_set(
    session_factory: sessionmaker[Session], init_data_dir: Path
) -> None:
    users = _RaceUserRepository()
    service = BootstrapService(users=users)

    def run_bootstrap(_index: int) -> None:
        try:
            bootstrap_application(
                session_factory,
                init_data_dir=init_data_dir,
                service=service,
            )
        finally:
            users.mark_winner_finished()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run_bootstrap, range(2)))

    assert results == [None, None]
    assert users.create_calls == 2
    assert len(users.initial_lookup_sessions) == 2
    assert len(users.verification_sessions) == 1
    assert all(
        users.verification_sessions[0] is not initial_session
        for initial_session in users.initial_lookup_sessions
    )
    with session_scope(session_factory) as session:
        assert session.scalar(select(func.count(User.id))) == 1
        assert session.scalar(select(func.count(Resource.id))) == 2


@pytest.mark.parametrize(
    ("workspaces_yaml", "agents_yaml"),
    [
        (
            WORKSPACES_YAML.replace(
                "resources:\n",
                "resources:\n  - key: growth-home\n"
                "    name: Duplicate\n"
                "    config:\n"
                "      workspace_type: agent_home\n"
                "      initial_agents_md: duplicate\n",
                1,
            ),
            AGENTS_YAML,
        ),
        (
            WORKSPACES_YAML,
            AGENTS_YAML.replace(
                "home_workspace_key: growth-home",
                "home_workspace_key: missing-workspace",
            ),
        ),
        (f"unexpected: true\n{WORKSPACES_YAML}", AGENTS_YAML),
        (
            WORKSPACES_YAML.replace(
                "workspace_type: agent_home",
                "workspace_type: agent_home\n      unexpected: true",
            ),
            AGENTS_YAML,
        ),
        (
            WORKSPACES_YAML,
            AGENTS_YAML.replace(
                "knowledge_scopes: []",
                "knowledge_scopes: []\n      unexpected: true",
            ),
        ),
    ],
    ids=[
        "duplicate-workspace-key",
        "unknown-home-workspace-key",
        "top-level-extra-field",
        "nested-workspace-extra-field",
        "nested-agent-extra-field",
    ],
)
def test_invalid_seed_variants_do_not_write_database(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    workspaces_yaml: str,
    agents_yaml: str,
) -> None:
    directory = tmp_path / "invalid-variant"
    directory.mkdir()
    _write_seed_files(
        directory,
        workspaces_yaml=workspaces_yaml,
        agents_yaml=agents_yaml,
    )

    with pytest.raises((ValidationError, ValueError)):
        bootstrap_application(session_factory, init_data_dir=directory)

    with session_scope(session_factory) as session:
        assert session.scalar(select(func.count(User.id))) == 0
        assert session.scalar(select(func.count(Resource.id))) == 0


@pytest.mark.parametrize(
    ("workspaces_yaml", "agents_yaml"),
    [
        (
            WORKSPACES_YAML.replace(
                "workspace_type: agent_home",
                "workspace_type: agent_home\n      navigation_prompt: forbidden",
            ),
            AGENTS_YAML,
        ),
        (
            WORKSPACES_YAML,
            AGENTS_YAML.replace(
                "default_model: null",
                "default_model:\n        provider: qwen",
            ),
        ),
        (
            WORKSPACES_YAML,
            AGENTS_YAML.replace(
                "knowledge_scopes: []",
                "knowledge_scopes:\n"
                "        - collection_id: handbook\n"
                "          document_ids: null\n"
                "        - collection_id: handbook\n"
                "          document_ids:\n"
                "            - one-document",
            ),
        ),
    ],
    ids=[
        "unknown-workspace-config-field",
        "invalid-model-reference",
        "duplicate-knowledge-scope",
    ],
)
def test_final_domain_schema_violations_roll_back_all_bootstrap_rows(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    workspaces_yaml: str,
    agents_yaml: str,
) -> None:
    directory = tmp_path / "invalid-domain-seed"
    directory.mkdir()
    _write_seed_files(
        directory,
        workspaces_yaml=workspaces_yaml,
        agents_yaml=agents_yaml,
    )

    with pytest.raises(ValidationError):
        bootstrap_application(session_factory, init_data_dir=directory)

    _assert_database_is_empty(session_factory)


@pytest.mark.parametrize(
    ("workspaces_yaml", "agents_yaml"),
    [
        (
            "resources: []\n",
            AGENTS_YAML.replace(
                "home_workspace_key: growth-home",
                "home_workspace_key: null",
            ),
        ),
        (WORKSPACES_YAML, "resources: []\n"),
    ],
    ids=["empty-workspaces", "empty-agents"],
)
def test_empty_seed_resource_collections_do_not_write_database(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    workspaces_yaml: str,
    agents_yaml: str,
) -> None:
    directory = tmp_path / "empty-resources"
    directory.mkdir()
    _write_seed_files(
        directory,
        workspaces_yaml=workspaces_yaml,
        agents_yaml=agents_yaml,
    )

    with pytest.raises((ValidationError, ValueError)):
        bootstrap_application(session_factory, init_data_dir=directory)

    _assert_database_is_empty(session_factory)


def test_duplicate_yaml_mapping_key_does_not_write_database(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    directory = tmp_path / "duplicate-yaml-key"
    directory.mkdir()
    duplicate_key_yaml = WORKSPACES_YAML.replace(
        "    name: 成长助手工作区\n",
        f"    name: 成长助手工作区\n    name: {SECRET_MARKER}\n",
    )
    _write_seed_files(directory, workspaces_yaml=duplicate_key_yaml)

    with pytest.raises(ValueError) as error:
        bootstrap_application(session_factory, init_data_dir=directory)

    assert SECRET_MARKER not in str(error.value)
    _assert_database_is_empty(session_factory)


def test_oversized_seed_file_is_rejected_before_parsing(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    directory = tmp_path / "oversized-seed"
    directory.mkdir()
    _write_seed_files(directory)
    oversized_comment = f"\n# {SECRET_MARKER}".encode() + b"x" * MAX_SEED_FILE_BYTES
    with (directory / "agents.yaml").open("ab") as seed_file:
        seed_file.write(oversized_comment)

    with pytest.raises(ValueError) as error:
        bootstrap_application(session_factory, init_data_dir=directory)

    assert SECRET_MARKER not in str(error.value)
    _assert_database_is_empty(session_factory)


def test_seed_resource_count_over_budget_does_not_write_database(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    directory = tmp_path / "too-many-resources"
    directory.mkdir()
    workspaces = [
        f"  - key: workspace-{index}\n"
        f"    name: Workspace {index}\n"
        "    config:\n"
        "      workspace_type: agent_home\n"
        f"      initial_agents_md: {SECRET_MARKER}\n"
        for index in range(MAX_SEED_RESOURCES + 1)
    ]
    agents_yaml = AGENTS_YAML.replace(
        "home_workspace_key: growth-home",
        "home_workspace_key: workspace-0",
    )
    _write_seed_files(
        directory,
        workspaces_yaml="resources:\n" + "".join(workspaces),
        agents_yaml=agents_yaml,
    )

    with pytest.raises((ValidationError, ValueError)) as error:
        bootstrap_application(session_factory, init_data_dir=directory)

    assert SECRET_MARKER not in str(error.value)
    _assert_database_is_empty(session_factory)


def test_exponential_yaml_alias_dag_is_rejected_without_echoing_seed_content(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    directory = tmp_path / "exponential-alias-dag"
    directory.mkdir()
    alias_chain = [
        f"        - &node0\n          value: {SECRET_MARKER}\n",
    ]
    for index in range(1, 21):
        alias_chain.append(
            f"        - &node{index}\n"
            f"          left: *node{index - 1}\n"
            f"          right: *node{index - 1}\n"
        )
    agents_yaml = f"""\
resources:
  - name: 成长助手
    config:
      system_prompt: 你是用户的通用成长助手。
      default_model: null
      home_workspace_key: growth-home
      knowledge_scopes:
{"".join(alias_chain)}"""
    _write_seed_files(directory, agents_yaml=agents_yaml)
    assert (directory / "agents.yaml").stat().st_size < 10_000

    with pytest.raises(ValueError) as error:
        bootstrap_application(session_factory, init_data_dir=directory)

    assert SECRET_MARKER not in str(error.value)
    _assert_database_is_empty(session_factory)


def test_yaml_depth_budget_is_enforced_without_echoing_seed_content(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    directory = tmp_path / "too-deep-yaml"
    directory.mkdir()
    nested_value = "[" * (MAX_YAML_DEPTH + 1) + SECRET_MARKER + "]" * (
        MAX_YAML_DEPTH + 1
    )
    agents_yaml = AGENTS_YAML.replace(
        "knowledge_scopes: []",
        f"knowledge_scopes:\n        - nested: {nested_value}",
    )
    _write_seed_files(directory, agents_yaml=agents_yaml)

    with pytest.raises(ValueError) as error:
        bootstrap_application(session_factory, init_data_dir=directory)

    assert SECRET_MARKER not in str(error.value)
    _assert_database_is_empty(session_factory)


@pytest.mark.parametrize(
    ("workspaces_yaml", "agents_yaml"),
    [
        (WORKSPACES_YAML.replace("name: 成长助手工作区", 'name: "   "'), AGENTS_YAML),
        (WORKSPACES_YAML.replace("key: growth-home", 'key: "   "'), AGENTS_YAML),
        (
            WORKSPACES_YAML.replace(
                "      initial_agents_md: |-\n"
                "        # 成长助手工作区\n\n"
                "        本目录保存用户明确要求长期沉淀的个人学习、练习与成长资料。\n",
                '      initial_agents_md: "   "\n',
            ),
            AGENTS_YAML,
        ),
        (WORKSPACES_YAML, AGENTS_YAML.replace("name: 成长助手", 'name: "   "')),
        (
            WORKSPACES_YAML,
            AGENTS_YAML.replace(
                "system_prompt: 你是用户的通用成长助手。",
                'system_prompt: "   "',
            ),
        ),
        (
            WORKSPACES_YAML.replace("key: growth-home", f"key: {'k' * 121}"),
            AGENTS_YAML,
        ),
        (
            WORKSPACES_YAML,
            AGENTS_YAML.replace(
                "system_prompt: 你是用户的通用成长助手。",
                f"system_prompt: {'p' * 100_001}",
            ),
        ),
        (
            WORKSPACES_YAML.replace(
                "      initial_agents_md: |-\n"
                "        # 成长助手工作区\n\n"
                "        本目录保存用户明确要求长期沉淀的个人学习、练习与成长资料。\n",
                f"      initial_agents_md: {'m' * 100_001}\n",
            ),
            AGENTS_YAML,
        ),
        (
            WORKSPACES_YAML.replace("workspace_type: agent_home", 'workspace_type: "   "'),
            AGENTS_YAML,
        ),
        (
            WORKSPACES_YAML.replace(
                "workspace_type: agent_home",
                f"workspace_type: {'t' * 33}",
            ),
            AGENTS_YAML,
        ),
    ],
    ids=[
        "blank-workspace-name",
        "blank-workspace-key",
        "blank-workspace-manifest",
        "blank-agent-name",
        "blank-agent-prompt",
        "overlong-workspace-key",
        "overlong-agent-prompt",
        "overlong-workspace-manifest",
        "blank-workspace-type",
        "overlong-workspace-type",
    ],
)
def test_seed_string_boundaries_roll_back_bootstrap(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    workspaces_yaml: str,
    agents_yaml: str,
) -> None:
    directory = tmp_path / "invalid-string-boundary"
    directory.mkdir()
    _write_seed_files(
        directory,
        workspaces_yaml=workspaces_yaml,
        agents_yaml=agents_yaml,
    )

    with pytest.raises(ValidationError):
        bootstrap_application(session_factory, init_data_dir=directory)

    with session_scope(session_factory) as session:
        assert session.scalar(select(func.count(User.id))) == 0
        assert session.scalar(select(func.count(Resource.id))) == 0


def test_loader_strips_outer_whitespace_and_preserves_internal_newlines(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "normalized-seed"
    directory.mkdir()
    _write_seed_files(
        directory,
        workspaces_yaml="""\
resources:
  - key: "  growth-home  "
    name: "  成长助手工作区  "
    config:
      workspace_type: "  agent_home  "
      initial_agents_md: |-

        # 成长助手工作区

        正文

""",
        agents_yaml="""\
resources:
  - name: "  成长助手  "
    config:
      system_prompt: |-

        第一段

        第二段

      default_model: null
      home_workspace_key: "  growth-home  "
      knowledge_scopes: []
""",
    )

    workspaces, agents = load_seed_resources(directory)

    assert workspaces[0].key == "growth-home"
    assert workspaces[0].name == "成长助手工作区"
    assert workspaces[0].config.workspace_type == "agent_home"
    assert workspaces[0].config.initial_agents_md == "# 成长助手工作区\n\n正文"
    assert agents[0].name == "成长助手"
    assert agents[0].config.system_prompt == "第一段\n\n第二段"
    assert agents[0].config.home_workspace_key == "growth-home"


def test_seed_loader_rejects_more_than_twenty_knowledge_scopes(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "too-many-agent-knowledge-scopes"
    directory.mkdir()
    scopes = "\n".join(
        f"        - collection_id: collection-{index}"
        for index in range(21)
    )
    _write_seed_files(
        directory,
        agents_yaml=AGENTS_YAML.replace(
            "knowledge_scopes: []",
            f"knowledge_scopes:\n{scopes}",
        ),
    )

    with pytest.raises(ValidationError):
        load_seed_resources(directory)


def test_database_failure_rolls_back_admin_and_resources(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    directory = tmp_path / "duplicate-resource-name"
    directory.mkdir()
    duplicate_names = AGENTS_YAML + AGENTS_YAML.replace(
        "resources:\n",
        "",
        1,
    )
    _write_seed_files(directory, agents_yaml=duplicate_names)
    users = _CountingUserRepository()
    service = BootstrapService(users=users)
    engine = session_factory.kw["bind"]
    integrity_errors: list[IntegrityError] = []

    def observe_integrity_error(exception_context) -> None:
        error = exception_context.sqlalchemy_exception
        if isinstance(error, IntegrityError):
            integrity_errors.append(error)

    event.listen(engine, "handle_error", observe_integrity_error)
    try:
        with pytest.raises(IntegrityError) as error:
            bootstrap_application(
                session_factory,
                init_data_dir=directory,
                service=service,
            )
    finally:
        event.remove(engine, "handle_error", observe_integrity_error)

    assert users.create_calls == 1
    assert len(integrity_errors) == 1
    assert error.value is integrity_errors[0]
    _assert_database_is_empty(session_factory)


def test_verification_failure_preserves_initial_integrity_error(
    session_factory: sessionmaker[Session], init_data_dir: Path
) -> None:
    users = _VerificationFailureUserRepository()
    service = BootstrapService(users=users)

    with pytest.raises(IntegrityError) as error:
        bootstrap_application(
            session_factory,
            init_data_dir=init_data_dir,
            service=service,
        )

    assert error.value is users.bootstrap_error
    assert error.value.__cause__ is users.verification_error
    assert len(users.lookup_sessions) == 2
    assert users.lookup_sessions[0] is not users.lookup_sessions[1]
    _assert_database_is_empty(session_factory)
