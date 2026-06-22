from __future__ import annotations

from sqlalchemy.orm import Session

from app.db import models


class WorkspaceSettingsRepository:
    def get_or_create(self, session: Session) -> models.WorkspaceSettings:
        settings = session.get(models.WorkspaceSettings, "default")
        if settings is None:
            settings = models.WorkspaceSettings(id="default")
            session.add(settings)
            session.flush()
        return settings
