from datetime import UTC, datetime

from sqlalchemy import inspect, select


def test_workspace_models_store_user_scoped_workspace_state(client) -> None:
    from app.db.models import Artifact, Conversation, Message, User
    from app.db.session import session_scope

    with session_scope(client.app.state.session_factory) as session:
        user = User(
            username="alice",
            password_hash="hash",
            settings_json={
                "schema_version": 1,
                "language": "zh-CN",
                "active_collection": "auto_reign_user_1",
            },
        )
        session.add(user)
        session.flush()
        artifact = Artifact(
            id="artifact-1",
            user_id=user.id,
            kind="knowledge",
            relative_path="knowledge/redis.md",
            content_hash="hash-1",
            revision=3,
            status_json={
                "processing_status": "needs_recovery",
                "index_status": "stale",
                "recovery_required": True,
                "recovery_reason": "missing_front_matter",
            },
            metadata_json={
                "source_refs": ["source:resume"],
                "evidence_refs": [],
                "language": "zh-CN",
                "origin": "human",
                "edited_by": "user",
            },
        )
        session.add(artifact)
        session.flush()
        conversation = Conversation(
            user_id=user.id,
            kind="learning",
            title="Redis",
            summary_json={"last_message": "缓存击穿"},
        )
        session.add(conversation)
        session.flush()
        message = Message(
            user_id=user.id,
            conversation_id=conversation.id,
            sequence=1,
            role="user",
            message_type="learning_note",
            content="缓存击穿",
        )
        session.add(message)
        session.flush()

        loaded_artifact = session.get(Artifact, "artifact-1")
        loaded_conversation = session.get(Conversation, conversation.id)
        assert loaded_artifact is not None
        assert loaded_artifact.user_id == user.id
        assert loaded_artifact.metadata_json["source_refs"] == ["source:resume"]
        assert loaded_artifact.status_json["processing_status"] == "needs_recovery"
        assert loaded_artifact.status_json["index_status"] == "stale"
        assert loaded_conversation is not None
        assert loaded_conversation.user_id == user.id
        assert loaded_conversation.messages[0].content == "缓存击穿"


def test_user_settings_store_active_collection_per_user(client) -> None:
    from app.db.models import User
    from app.db.session import session_scope

    with session_scope(client.app.state.session_factory) as session:
        alice = User(
            username="alice",
            password_hash="hash",
            settings_json={"active_collection": "auto_reign_user_1"},
        )
        bob = User(
            username="bob",
            password_hash="hash",
            settings_json={"active_collection": "auto_reign_user_2"},
        )
        session.add_all([alice, bob])
        session.flush()

        rows = list(session.scalars(select(User).order_by(User.id)))

    assert [row.settings_json["active_collection"] for row in rows] == [
        "auto_reign_user_1",
        "auto_reign_user_2",
    ]


def test_workspace_front_matter_persists_recovery_semantics() -> None:
    from app.schemas.workspace import ArtifactFrontMatter

    metadata = ArtifactFrontMatter(
        id="recovered-1",
        kind="practice",
        language="zh-CN",
        revision=1,
        created_at=datetime(2026, 6, 22, tzinfo=UTC),
        updated_at=datetime(2026, 6, 22, tzinfo=UTC),
        origin="human",
        edited_by="user",
        recovery_required=True,
        recovery_reason="missing_front_matter",
    )

    assert metadata.recovery_required is True
    assert metadata.recovery_reason == "missing_front_matter"


def test_workspace_tables_exist_in_test_schema(client) -> None:
    from app.db.session import session_scope

    with session_scope(client.app.state.session_factory) as session:
        tables = set(inspect(session.get_bind()).get_table_names())

    assert {"users", "artifacts", "conversations", "messages"} <= tables
    assert "workspace_settings" not in tables
    assert "processing_jobs" not in tables
