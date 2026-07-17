from __future__ import annotations

from datetime import UTC, datetime

from langchain_core.messages import ToolMessage
from sqlalchemy.orm import sessionmaker

from app.schemas.agents import AgentConfig
from app.services.agent_service import ResolvedAgentConfig, freeze_json
from app.services.langchain_tool_adapter import (
    CapabilityBaseTool,
    build_langchain_tools,
)
from app.services.runtime_types import CapabilityContext, ToolDefinition, ToolResult
from app.services.tool_registry import ToolRegistry


class _Provider:
    def prompt_modules(self, context: CapabilityContext) -> tuple[str, ...]:
        del context
        return ()

    def tool_definitions(
        self,
        context: CapabilityContext,
    ) -> tuple[ToolDefinition, ...]:
        del context
        return (
            ToolDefinition(
                name="read_file",
                description="Read a file.",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
        )

    def execute(self, call, context: CapabilityContext) -> ToolResult:
        assert context.user_id == 7
        return ToolResult(
            call_id=call.id,
            content='{"content":"safe"}',
            metadata={"tool": call.name},
        )


def _context() -> CapabilityContext:
    config = AgentConfig(system_prompt="Help.")
    return CapabilityContext(
        user_id=7,
        agent_config=ResolvedAgentConfig(
            agent_id="agent-1",
            owner_user_id=7,
            system_prompt="Help.",
            default_model=None,
            home_workspace=None,
            knowledge_scopes=(),
            config_json=freeze_json(config.model_dump(mode="json", exclude_none=False)),
            updated_at=datetime.now(UTC),
            config_hash="test-hash",
        ),
        session_factory=sessionmaker(),
        token_budget=2_048,
    )


def test_adapter_preserves_public_schema_and_dispatches_private_context() -> None:
    context = _context()
    snapshot = ToolRegistry((_Provider(),)).bind(context)
    tools = build_langchain_tools(snapshot, context)

    assert len(tools) == 1
    tool = tools[0]
    assert isinstance(tool, CapabilityBaseTool)
    assert tool.name == "read_file"
    assert tool.description == "Read a file."
    assert tool.args_schema == snapshot.definitions[0].input_schema
    message = tool.invoke(
        {
            "type": "tool_call",
            "id": "call-from-graph",
            "name": "read_file",
            "args": {"path": "notes.md"},
        }
    )

    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "call-from-graph"
    assert message.content == '{"content":"safe"}'
    assert isinstance(message.artifact, ToolResult)
    assert message.artifact.call_id == "call-from-graph"
    assert message.artifact.metadata == {"tool": "read_file"}
    assert not hasattr(tool, "session_factory")
    assert not hasattr(tool, "user_id")
