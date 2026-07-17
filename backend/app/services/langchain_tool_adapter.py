from __future__ import annotations

from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

from app.services.runtime_types import CapabilityContext, ToolCall, ToolResult
from app.services.tool_registry import ToolRegistrySnapshot, ToolSpec


class CapabilityBaseTool(BaseTool):
    """Expose one already-authorized capability as a LangChain tool.

    The adapter is deliberately created from a turn-bound registry snapshot.
    LangChain receives only the public name, description and JSON schema; the
    provider and runtime context remain private application objects.
    """

    _spec: ToolSpec = PrivateAttr()
    _context: CapabilityContext = PrivateAttr()

    @classmethod
    def from_spec(
        cls,
        *,
        spec: ToolSpec,
        context: CapabilityContext,
    ) -> CapabilityBaseTool:
        definition = spec.definition
        tool = cls(
            name=definition.name,
            description=definition.description,
            args_schema=dict(definition.input_schema),
            response_format="content_and_artifact",
        )
        tool._spec = spec
        tool._context = context
        return tool

    def _to_args_and_kwargs(
        self,
        tool_input: str | dict[str, object],
        tool_call_id: str | None,
    ) -> tuple[tuple[str, ...], dict[str, object]]:
        if not tool_call_id:
            raise ValueError("runtime tool calls require a tool_call_id")
        args, kwargs = super()._to_args_and_kwargs(tool_input, tool_call_id)
        kwargs["runtime_tool_call_id"] = tool_call_id
        return args, kwargs

    def _run(
        self,
        *,
        runtime_tool_call_id: str,
        **kwargs: object,
    ) -> tuple[str, ToolResult]:
        result = self._spec.execute(
            ToolCall(
                id=runtime_tool_call_id,
                name=self.name,
                arguments=kwargs,
            ),
            self._context,
        )
        return result.content, result


def build_langchain_tools(
    snapshot: ToolRegistrySnapshot,
    context: CapabilityContext,
) -> tuple[BaseTool, ...]:
    """Build LangChain tools for a single prepared turn.

    ``args_schema`` is passed as the original JSON Schema so required fields,
    property descriptions and additionalProperties remain visible to a model.
    """

    return tuple(
        CapabilityBaseTool.from_spec(spec=spec, context=context)
        for spec in snapshot.specs.values()
    )
