from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


class UserRepository:
    def list_users(self, session: Session) -> list[models.User]:
        return list(session.scalars(select(models.User).order_by(models.User.username)))

    def get_by_username(self, session: Session, username: str) -> models.User | None:
        return session.scalar(select(models.User).where(models.User.username == username))

    def get_for_update(self, session: Session, user_id: int) -> models.User | None:
        return session.scalar(
            select(models.User).where(models.User.id == user_id).with_for_update()
        )

    def create(self, session: Session, **values: object) -> models.User:
        user = models.User(**values)
        session.add(user)
        session.flush()
        return user
