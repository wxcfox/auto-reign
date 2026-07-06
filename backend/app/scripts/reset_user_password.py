from __future__ import annotations

import argparse
import getpass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.passwords import hash_password
from app.db import models
from app.db.session import create_engine_for_settings, make_session_factory, session_scope


def reset_user_password(
    *,
    session_factory: sessionmaker[Session],
    username: str,
) -> None:
    with session_scope(session_factory) as session:
        user = session.scalar(select(models.User).where(models.User.username == username))
        if user is None:
            raise SystemExit(f"User not found: {username}")

        password = getpass.getpass("New password: ")
        confirmation = getpass.getpass("Confirm new password: ")
        if password != confirmation:
            raise SystemExit("Passwords do not match.")
        if len(password) < 12:
            raise SystemExit("Password must contain at least 12 characters.")

        user.password_hash = hash_password(password)
        user.token_version += 1
        session.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset a local user's password.")
    parser.add_argument("--username", required=True)
    args = parser.parse_args()

    engine = create_engine_for_settings(get_settings())
    try:
        reset_user_password(
            session_factory=make_session_factory(engine),
            username=args.username,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
