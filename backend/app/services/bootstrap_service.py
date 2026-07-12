import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import conflict
from app.core.init_data import load_seed_resources
from app.core.passwords import hash_password
from app.db import models
from app.db.session import session_scope
from app.repositories.user_repository import UserRepository
from app.schemas.agents import AgentConfig
from app.schemas.workspaces import WorkspaceConfig


INITIAL_ADMIN_USERNAME = "admin"


class BootstrapService:
    def __init__(self, users: UserRepository | None = None) -> None:
        self.users = users or UserRepository()

    def ensure_initialized(self, session: Session, *, init_data_dir: Path) -> None:
        workspaces, agents = load_seed_resources(init_data_dir)
        admin = self.users.get_by_username(session, INITIAL_ADMIN_USERNAME)
        if admin is None:
            admin = self.users.create(
                session,
                username=INITIAL_ADMIN_USERNAME,
                display_name="Admin",
                password_hash=hash_password(secrets.token_urlsafe(48)),
                role="admin",
                is_active=True,
                credential_bootstrap_status="pending",
                settings_json={},
            )
        if (
            admin.role != "admin"
            or admin.credential_bootstrap_status not in {"pending", "completed"}
        ):
            raise RuntimeError("fixed admin record is invalid")
        if admin.seed_initialized_at is not None:
            return
        by_key: dict[str, models.Resource] = {}
        for item in workspaces:
            config = WorkspaceConfig.model_validate(
                item.config.model_dump(mode="json")
            )
            resource = models.Resource(
                user_id=0,
                resource_type="workspace",
                name=item.name,
                config_json=config.model_dump(mode="json"),
            )
            session.add(resource)
            session.flush()
            by_key[item.key] = resource
        for item in agents:
            config = item.config.model_dump(mode="json", exclude={"home_workspace_key"})
            key = item.config.home_workspace_key
            config["home_workspace_id"] = by_key[key].id if key is not None else None
            validated_config = AgentConfig.model_validate(config)
            session.add(
                models.Resource(
                    user_id=0,
                    resource_type="agent",
                    name=item.name,
                    config_json=validated_config.model_dump(mode="json"),
                )
            )
        admin.seed_initialized_at = models._now()
        session.flush()

    def is_fixed_admin_initialized(self, session: Session) -> bool:
        admin = self.users.get_by_username(session, INITIAL_ADMIN_USERNAME)
        return (
            admin is not None
            and admin.username == INITIAL_ADMIN_USERNAME
            and admin.role == "admin"
            and admin.credential_bootstrap_status in {"pending", "completed"}
            and admin.seed_initialized_at is not None
        )

    def setup_initial_admin_password(
        self,
        session: Session,
        *,
        password: str,
    ) -> models.User:
        admin = session.scalar(
            select(models.User)
            .where(models.User.username == INITIAL_ADMIN_USERNAME)
            .with_for_update()
        )
        if admin is None or admin.credential_bootstrap_status != "pending":
            raise conflict(
                "admin_password_already_initialized",
                "The initial administrator password is already initialized.",
            )
        admin.password_hash = hash_password(password)
        admin.credential_bootstrap_status = "completed"
        admin.token_version += 1
        session.flush()
        return admin


def bootstrap_application(
    session_factory: sessionmaker[Session],
    *,
    init_data_dir: Path,
    service: BootstrapService | None = None,
) -> None:
    bootstrap = service or BootstrapService()
    try:
        with session_scope(session_factory) as session:
            bootstrap.ensure_initialized(
                session,
                init_data_dir=init_data_dir,
            )
    except IntegrityError as bootstrap_error:
        try:
            with session_scope(session_factory) as verification_session:
                if bootstrap.is_fixed_admin_initialized(verification_session):
                    return
        except Exception as verification_error:
            raise bootstrap_error from verification_error
        raise
