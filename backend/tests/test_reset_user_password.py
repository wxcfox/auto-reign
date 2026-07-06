from app.core.passwords import verify_password
from app.db import models
from app.db.session import session_scope


def test_reset_user_password_updates_hash_and_revokes_tokens(client, monkeypatch):
    from app.scripts.reset_user_password import reset_user_password

    with session_scope(client.app.state.session_factory) as session:
        session.add(
            models.User(
                username="alice",
                password_hash="old",
                display_name="Alice",
            )
        )

    monkeypatch.setattr(
        "getpass.getpass",
        lambda _prompt: "new correct horse battery staple",
    )

    reset_user_password(
        session_factory=client.app.state.session_factory,
        username="alice",
    )

    with session_scope(client.app.state.session_factory) as session:
        user = session.query(models.User).filter_by(username="alice").one()
        assert user.token_version == 2
        assert verify_password("new correct horse battery staple", user.password_hash)
