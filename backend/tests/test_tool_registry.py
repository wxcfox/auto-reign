from __future__ import annotations

import pytest
from sqlalchemy.orm import sessionmaker
from datetime import UTC, datetime

from app.services.agent_service import ResolvedAgentConfig, freeze_json
from app.schemas.agents import AgentConfig
from app.services.runtime_types import CapabilityContext, ToolCall, ToolDefinition, ToolResult
from app.services.tool_registry import ToolRegistry


def _context() -> CapabilityContext:
    return CapabilityContext(
        user_id=7,
        agent_config=ResolvedAgentConfig(
            agent_id="agent-1",
            owner_user_id=7,
            system_prompt="Help.",
            default_model=None,
            home_workspace=None,
            knowledge_scopes=(),
            config_json=freeze_json(
                AgentConfig(system_prompt="Help.").model_dump(
                    mode="json", exclude_none=False
                )
            ),
            updated_at=datetime.now(UTC),
            config_hash="test-config-hash",
        ),
        session_factory=sessionmaker(),
        token_budget=2_048,
    )


class Provider:
    def __init__(self, name: str, module: str = "") -> None:
        self.name = name
        self.module = module
        self.calls: list[ToolCall] = []

    def prompt_modules(self, context: CapabilityContext) -> tuple[str, ...]:
        del context
        return (self.module,) if self.module else ()

    def tool_definitions(
        self,
        context: CapabilityContext,
    ) -> tuple[ToolDefinition, ...]:
        del context
        return (
            ToolDefinition(
                name=self.name,
                description=f"Use {self.name}.",
                input_schema={"type": "object"},
            ),
        )

    def execute(self, call: ToolCall, context: CapabilityContext) -> ToolResult:
        assert context.user_id == 7
        self.calls.append(call)
        return ToolResult(call_id=call.id, content='{"ok":true}')


def test_bind_preserves_provider_order_and_dispatches_to_bound_owner() -> None:
    first = Provider("read_file", "agent_home")
    second = Provider("search_knowledge", "knowledge_base")

    registry = ToolRegistry((first, second))
    snapshot = registry.bind(_context())

    assert [item.name for item in snapshot.definitions] == [
        "read_file",
        "search_knowledge",
    ]
    assert snapshot.prompt_modules == ("agent_home", "knowledge_base")
    with pytest.raises(TypeError):
        snapshot.specs["other"] = snapshot.specs["read_file"]  # type: ignore[index]
    result = snapshot.execute(
        ToolCall(id="call-1", name="read_file", arguments={"path": "a.md"}),
        _context(),
    )
    assert result.is_error is False
    assert first.calls[0].id == "call-1"
    assert second.calls == []


def test_bind_rejects_duplicate_tool_names() -> None:
    with pytest.raises(RuntimeError, match="duplicate capability tool: read_file"):
        ToolRegistry((Provider("read_file"), Provider("read_file"))).bind(_context())


def test_unregistered_tool_returns_stable_error_result() -> None:
    result = ToolRegistry((Provider("read_file"),)).bind(_context()).execute(
        ToolCall(id="call-404", name="delete_file", arguments={}),
        _context(),
    )

    assert result.call_id == "call-404"
    assert result.is_error is True
    assert result.metadata == {"tool": "delete_file", "code": "tool_not_found"}
    assert result.content == (
        '{"code":"tool_not_found","message":"The requested tool is unavailable."}'
    )
