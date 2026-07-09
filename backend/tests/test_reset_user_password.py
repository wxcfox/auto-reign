import pytest

from app.core.passwords import hash_password, verify_password
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


def test_reset_user_password_rejects_mismatched_confirmation(client, monkeypatch):
    from app.scripts.reset_user_password import reset_user_password

    old_hash = hash_password("old correct horse battery staple")
    with session_scope(client.app.state.session_factory) as session:
        session.add(
            models.User(
                username="alice",
                password_hash=old_hash,
                display_name="Alice",
            )
        )

    passwords = iter(
        [
            "new correct horse battery staple",
            "different correct horse battery staple",
        ]
    )
    monkeypatch.setattr("getpass.getpass", lambda _prompt: next(passwords))

    with pytest.raises(SystemExit) as error:
        reset_user_password(
            session_factory=client.app.state.session_factory,
            username="alice",
        )

    assert str(error.value) == "Passwords do not match."
    with session_scope(client.app.state.session_factory) as session:
        user = session.query(models.User).filter_by(username="alice").one()
        assert user.token_version == 1
        assert user.password_hash == old_hash


def test_reset_user_password_rejects_short_password(client, monkeypatch):
    from app.scripts.reset_user_password import reset_user_password

    old_hash = hash_password("old correct horse battery staple")
    with session_scope(client.app.state.session_factory) as session:
        session.add(
            models.User(
                username="alice",
                password_hash=old_hash,
                display_name="Alice",
            )
        )

    monkeypatch.setattr("getpass.getpass", lambda _prompt: "short")

    with pytest.raises(SystemExit) as error:
        reset_user_password(
            session_factory=client.app.state.session_factory,
            username="alice",
        )

    assert str(error.value) == "Password must contain at least 6 characters."
    with session_scope(client.app.state.session_factory) as session:
        user = session.query(models.User).filter_by(username="alice").one()
        assert user.token_version == 1
        assert user.password_hash == old_hash


def test_reset_user_password_unknown_user_exits_before_prompt(client, monkeypatch):
    from app.scripts.reset_user_password import reset_user_password

    def fail_prompt(_prompt):
        raise AssertionError("missing users should not prompt for a password")

    monkeypatch.setattr("getpass.getpass", fail_prompt)

    with pytest.raises(SystemExit) as error:
        reset_user_password(
            session_factory=client.app.state.session_factory,
            username="alice",
        )

    assert str(error.value) == "User not found: alice"
