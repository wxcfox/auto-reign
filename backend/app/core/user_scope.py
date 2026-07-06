from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.db.models import User


@dataclass(frozen=True)
class UserScope:
    user_id: int
    workspace_root: Path
    tmp_root: Path
    exports_root: Path
    qdrant_prefix: str


def build_user_scope(settings: Settings, user: User) -> UserScope:
    user_root = settings.data_dir / "users" / str(user.id)
    tmp_root = user_root / "tmp"
    exports_root = user_root / "exports"
    tmp_root.mkdir(parents=True, exist_ok=True)
    exports_root.mkdir(parents=True, exist_ok=True)
    return UserScope(
        user_id=user.id,
        workspace_root=user_root / "workspace",
        tmp_root=tmp_root,
        exports_root=exports_root,
        qdrant_prefix=f"auto_reign_user_{user.id}",
    )
