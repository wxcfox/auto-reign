"""Multi-source ReAct orchestration behaviour.

The model owns intent judgement; the harness owns tool scope, tool call/result
correlation, error classification and persistence. These tests script a model
decision and assert the harness executes exactly that decision, and that the
harness gives the model everything it needs to make the decision without any
hardcoded data-source routing.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
import json

from fastapi import HTTPException
import pytest
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.schemas.agents import AgentConfig
from app.schemas.knowledge_collections import KnowledgeCollectionConfig
from app.schemas.workspaces import WorkspaceConfig
from app.services import agent_runtime
from app.services.agent_home_service import AgentHomeService
from app.services.agent_runtime import AgentRuntime, RuntimeTurn
from app.services.agent_service import (
    ResolvedAgentConfig,
    ResolvedAgentHome,
    ResolvedKnowledgeScope,
    freeze_json,
)
from app.services.capability_source_prompt import BLOCK_END, BLOCK_START
from app.services.context_assembler import ContextAssembler
from app.services.knowledge_retrieval_service import (
    KnowledgeSearchResult,
    serialize_knowledge_result,
)
from app.services.model_service import ModelService
from app.services.platform_prompt_service import PlatformPromptService
from app.services.runtime_event_reducer import RuntimeEventReducer
from app.services.runtime_types import (
    AssistantMessageEvent,
    CapabilityContext,
    CapabilityProvider,
    ProviderCallMetrics,
    RuntimeObserver,
    RuntimeTaskTurn,
    RuntimeTerminalError,
    RuntimeUserTurn,
    TextDeltaEvent,
    ToolCall,
    ToolDefinition,
    ToolResult,
    ToolResultEvent,
    ToolStartEvent,
)
from app.services.task_execution_service import _safe_execution_error_code
from app.services.token_counter import RuntimeTokenCounter
from app.tools.knowledge import KnowledgeCapabilityProvider
from tests.fake_object_store import FakeObjectStore
from tests.fakes import (
    RecordingRetrieval,
    RecordingScopeService,
    RecordingSessionFactory,
)


def _ignore_provider_metrics(_metrics: ProviderCallMetrics) -> None:
    return None


class ScriptedModel:
    """A model whose decision for each ReAct round is fixed in advance."""

    def __init__(self, rounds: list[tuple[str | ToolCall, ...]]) -> None:
        self.rounds = rounds
        self.calls: list[dict[str, object]] = []

    def stream_turn(
        self,
        messages: list[dict[str, object]],
        *,
        provider: str,
        model: str,
        call_index: int,
        observer: RuntimeObserver,
        tools: tuple[ToolDefinition, ...] | None = None,
    ) -> Iterator[str | ToolCall]:
        del observer
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "call_index": call_index,
                "tools": tools,
            }
        )
        return iter(self.rounds.pop(0))


class RecordingCapabilityProvider:
    """A capability provider that records what the harness actually executed."""

    def __init__(
        self,
        *,
        module: str,
        definitions: tuple[ToolDefinition, ...],
        results: dict[str, ToolResult] | None = None,
    ) -> None:
        self.module = module
        self._definitions = definitions
        self.results = results or {}
        self.executed: list[ToolCall] = []

    def prompt_modules(self, context: CapabilityContext) -> tuple[str, ...]:
        del context
        return (self.module,)

    def tool_definitions(
        self,
        context: CapabilityContext,
    ) -> tuple[ToolDefinition, ...]:
        del context
        return self._definitions

    def execute(self, call: ToolCall, context: CapabilityContext) -> ToolResult:
        del context
        self.executed.append(call)
        prepared = self.results.get(call.id)
        if prepared is not None:
            return prepared
        return ToolResult(
            call_id=call.id,
            content=json.dumps({"ok": call.name}, separators=(",", ":")),
            metadata={"tool": call.name},
        )


def _tool(name: str, description: str) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
        },
    )


def _knowledge_provider(
    results: dict[str, ToolResult] | None = None,
) -> RecordingCapabilityProvider:
    return RecordingCapabilityProvider(
        module="knowledge_base",
        definitions=(
            _tool(
                "search_knowledge",
                "Search the read-only knowledge collections bound to this Agent.",
            ),
        ),
        results=results,
    )


def _workspace_provider(
    results: dict[str, ToolResult] | None = None,
) -> RecordingCapabilityProvider:
    return RecordingCapabilityProvider(
        module="agent_home",
        definitions=(
            _tool("list_files", "List the direct children of one directory."),
            _tool("read_file", "Read one UTF-8 Agent Home file and its ETag."),
        ),
        results=results,
    )


def _agent_config(*, with_home: bool = False) -> ResolvedAgentConfig:
    frozen = freeze_json(
        AgentConfig(system_prompt="用简体中文回答。").model_dump(
            mode="json",
            exclude_none=False,
        )
    )
    assert isinstance(frozen, Mapping)
    collection_config = freeze_json(
        KnowledgeCollectionConfig().model_dump(mode="json", exclude_none=False)
    )
    assert isinstance(collection_config, Mapping)
    home = (
        ResolvedAgentHome(
            workspace_id="workspace-1",
            name="成长助手工作区",
            owner_user_id=7,
            initial_agents_md="# 成长助手工作区",
            config_json=_frozen_workspace_config(),
            updated_at=datetime.now(UTC),
        )
        if with_home
        else None
    )
    return ResolvedAgentConfig(
        agent_id="agent-1",
        owner_user_id=7,
        system_prompt="用简体中文回答。",
        default_model=None,
        home_workspace=home,
        knowledge_scopes=(
            ResolvedKnowledgeScope(
                collection_id="collection-1",
                name="成长助手资料库",
                owner_user_id=7,
                document_ids=None,
                document_names=None,
                config_json=collection_config,
                updated_at=datetime.now(UTC),
            ),
        ),
        config_json=frozen,
        updated_at=datetime.now(UTC),
        config_hash="test-config-hash",
    )


def _runtime(
    model: ScriptedModel,
    providers: tuple[CapabilityProvider, ...],
    *,
    token_budget: int = 40_000,
) -> AgentRuntime:
    counter = RuntimeTokenCounter(image_input_token_reserve=1_024)
    return AgentRuntime(
        model_service=model,  # type: ignore[arg-type]
        prompt_service=PlatformPromptService(),
        context_assembler=ContextAssembler(
            token_budget=token_budget,
            token_counter=counter,
        ),
        agent_home=AgentHomeService(store=FakeObjectStore()),
        token_counter=counter,
        tool_result_token_reserve=1_024,
        capability_providers=providers,
    )


def _frozen_workspace_config() -> Mapping[str, object]:
    frozen = freeze_json(
        WorkspaceConfig(
            workspace_type="agent_home",
            initial_agents_md="# 成长助手工作区",
        ).model_dump(mode="json", exclude_none=False)
    )
    assert isinstance(frozen, Mapping)
    return frozen


def _turn_for(
    question: str,
    *,
    with_home: bool = False,
    token_budget: int = 40_000,
) -> RuntimeTurn:
    return RuntimeTurn(
        context=CapabilityContext(
            user_id=7,
            agent_config=_agent_config(with_home=with_home),
            session_factory=sessionmaker(),
            token_budget=token_budget,
        ),
        agent_prompt="用简体中文回答。",
        provider="qwen",
        model="qwen3.7-plus",
        turns=(
            RuntimeTaskTurn(
                user=RuntimeUserTurn(message_id="m-1", text=question),
            ),
        ),
    )


def _stream(
    model: ScriptedModel,
    providers: tuple[CapabilityProvider, ...],
    question: str,
    *,
    token_budget: int = 40_000,
    with_home: bool = False,
    max_tool_rounds: int | None = None,
) -> list[object]:
    runtime = _runtime(model, providers, token_budget=token_budget)
    if max_tool_rounds is not None:
        runtime.configure_max_tool_rounds(max_tool_rounds)
    prepared = runtime.prepare_turn(
        _turn_for(question, with_home=with_home, token_budget=token_budget)
    )
    return list(runtime.stream_turn(prepared, observer=_ignore_provider_metrics))


def _executed_names(
    *providers: RecordingCapabilityProvider,
) -> list[str]:
    return [call.name for provider in providers for call in provider.executed]


def test_a_plain_question_runs_no_tool_even_with_both_sources_bound() -> None:
    model = ScriptedModel([("三", "个", "字",)])
    knowledge = _knowledge_provider()
    workspace = _workspace_provider()

    events = _stream(model, (workspace, knowledge), "帮我把这句话改写得更简洁")

    assert _executed_names(knowledge, workspace) == []
    assert [type(event) for event in events] == [
        TextDeltaEvent,
        TextDeltaEvent,
        TextDeltaEvent,
        AssistantMessageEvent,
    ]
    # Both capabilities were still offered; the model simply chose not to use them.
    assert {definition.name for definition in model.calls[0]["tools"]} == {
        "list_files",
        "read_file",
        "search_knowledge",
    }


def test_an_explicit_knowledge_request_reaches_only_the_knowledge_tool() -> None:
    call = ToolCall(id="c-1", name="search_knowledge", arguments={"value": "考纲"})
    model = ScriptedModel([(call,), ("好的",)])
    knowledge = _knowledge_provider()
    workspace = _workspace_provider()

    _stream(model, (workspace, knowledge), "查资料库里的考纲")

    assert _executed_names(knowledge) == ["search_knowledge"]
    assert workspace.executed == []


def test_an_explicit_workspace_request_reaches_only_the_workspace_tool() -> None:
    call = ToolCall(id="c-1", name="read_file", arguments={"value": "log.md"})
    model = ScriptedModel([(call,), ("好的",)])
    knowledge = _knowledge_provider()
    workspace = _workspace_provider()

    _stream(model, (workspace, knowledge), "读一下工作区的 log.md")

    assert _executed_names(workspace) == ["read_file"]
    assert knowledge.executed == []


def test_an_ambiguous_request_gives_the_model_both_sources_and_the_routing_contract() -> None:
    # No source is named. The harness must not route; it must hand the model
    # every bound source, each source's own prompt module, and the shared tool
    # orchestration contract, then execute whichever source the model picks.
    call = ToolCall(id="c-1", name="read_file", arguments={"value": "log.md"})
    model = ScriptedModel([(call,), ("好的",)])
    knowledge = _knowledge_provider()
    workspace = _workspace_provider()

    _stream(model, (workspace, knowledge), "根据我的记录给我出一题")

    platform_prompt = model.calls[0]["messages"][0]["content"]
    assert isinstance(platform_prompt, str)
    assert "# 工具与数据源使用协议" in platform_prompt
    assert "# 资料库（Knowledge）边界" in platform_prompt
    assert "# Agent Home 文件边界" in platform_prompt
    # The concrete bound sources are named, so the model can tell them apart.
    assert "[CAPABILITY_SOURCES]" in platform_prompt
    assert "成长助手资料库" in platform_prompt
    # The reusable rules themselves stay free of business terms; only the
    # per-turn source descriptor carries user-specific names.
    rules = platform_prompt.split("[CAPABILITY_SOURCES]")[0]
    for hardcoded in ("学习记录", "简历", "成长助手"):
        assert hardcoded not in rules
    assert _executed_names(workspace) == ["read_file"]


class SourceAwareModel(ScriptedModel):
    """A model that routes from the source descriptor it actually received.

    It stands in for real intent judgement: it reads `[CAPABILITY_SOURCES]` and
    the tool list out of the system prompt it was given, matches the user's
    words against the bound source labels, and only then picks a tool. If the
    harness fails to describe its sources, this model cannot route at all.
    """

    def __init__(self) -> None:
        super().__init__([])
        self.decisions: list[str] = []
        self.seen_source_labels: list[list[str]] = []
        self._round = 0
        self._queried: set[str] = set()

    def stream_turn(self, messages, **kwargs):  # type: ignore[override]
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "call_index": kwargs["call_index"],
                "tools": kwargs["tools"],
            }
        )
        system_prompt = messages[0]["content"]
        assert isinstance(system_prompt, str)
        labels = _source_labels(system_prompt)
        self.seen_source_labels.append(labels)
        available = {definition.name for definition in (kwargs["tools"] or ())}
        question = _last_user_text(messages)
        self._round += 1
        return iter(self._decide(question, labels, available))

    def _decide(
        self,
        question: str,
        labels: list[str],
        available: set[str],
    ) -> tuple[str | ToolCall, ...]:
        # "记录" belongs to the file source the user keeps; "资料" belongs to the
        # curated reference source. Both are matched against the descriptor
        # labels, so nothing here depends on a platform-side keyword table.
        # Each source is queried at most once, as the contract requires.
        wants_records = "记录" in question and any("工作区" in label for label in labels)
        wants_reference = "资料" in question and any("资料库" in label for label in labels)
        if wants_records and "workspace" not in self._queried and "read_file" in available:
            self._queried.add("workspace")
            self.decisions.append("workspace")
            return (
                ToolCall(
                    id=f"c-{self._round}",
                    name="read_file",
                    arguments={"value": "记录"},
                ),
            )
        if (
            wants_reference
            and "knowledge" not in self._queried
            and "search_knowledge" in available
        ):
            self._queried.add("knowledge")
            self.decisions.append("knowledge")
            return (
                ToolCall(
                    id=f"c-{self._round}",
                    name="search_knowledge",
                    arguments={"value": "资料"},
                ),
            )
        self.decisions.append("answer")
        return ("基于已取得的证据作答",)


def _source_labels(system_prompt: str) -> list[str]:
    # The descriptor is appended last, so the real block is the trailing one.
    # The capability prompt modules also mention the marker by name, which is
    # why this cannot simply search for the first occurrence.
    if not system_prompt.rstrip().endswith(BLOCK_END):
        return []
    block = system_prompt.rsplit(BLOCK_START, 1)[1].rsplit(BLOCK_END, 1)[0]
    payload = json.loads(block.strip().splitlines()[-1])
    labels: list[str] = []
    workspace = payload.get("workspace")
    if isinstance(workspace, dict):
        labels.append(str(workspace.get("name", "")))
    for entry in payload.get("knowledge_collections", []):
        if isinstance(entry, dict):
            labels.append(str(entry.get("name", "")))
    return labels


def _last_user_text(messages: list[dict[str, object]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            return message["content"]
    return ""


def test_the_descriptor_is_present_whenever_the_routing_contract_is() -> None:
    # `tool_use` tells the model to answer "what is bound?" from the descriptor,
    # so the two must never be injected independently. Tools can only exist when
    # a capability is bound, which is exactly when a source can be described.
    model = ScriptedModel([("好的",)])

    _stream(
        model,
        (_workspace_provider(), _knowledge_provider()),
        "绑定了哪些来源",
        with_home=True,
    )

    system_prompt = model.calls[0]["messages"][0]["content"]
    assert isinstance(system_prompt, str)
    assert "# 工具与数据源使用协议" in system_prompt
    assert _source_labels(system_prompt) == ["成长助手工作区", "成长助手资料库"]


def test_a_plain_chat_without_tools_gets_neither_contract_nor_descriptor() -> None:
    model = ScriptedModel([("好的",)])

    _stream(model, (), "你好")

    system_prompt = model.calls[0]["messages"][0]["content"]
    assert isinstance(system_prompt, str)
    assert "# 工具与数据源使用协议" not in system_prompt
    assert BLOCK_START not in system_prompt


def test_a_model_can_route_to_the_workspace_from_the_source_descriptor() -> None:
    model = SourceAwareModel()
    knowledge = _knowledge_provider()
    workspace = _workspace_provider()

    _stream(
        model,
        (workspace, knowledge),
        "根据我的记录给我抽一题",
        with_home=True,
    )

    assert model.seen_source_labels[0] == ["成长助手工作区", "成长助手资料库"]
    assert model.decisions == ["workspace", "answer"]
    assert _executed_names(workspace) == ["read_file"]
    assert knowledge.executed == []


def test_a_model_can_route_to_knowledge_from_the_source_descriptor() -> None:
    model = SourceAwareModel()
    knowledge = _knowledge_provider()
    workspace = _workspace_provider()

    _stream(
        model,
        (workspace, knowledge),
        "根据资料库的资料给我抽一题",
        with_home=True,
    )

    assert model.decisions == ["knowledge", "answer"]
    assert _executed_names(knowledge) == ["search_knowledge"]
    assert workspace.executed == []


def test_a_model_spans_both_sources_when_the_user_asks_for_both() -> None:
    model = SourceAwareModel()
    knowledge = _knowledge_provider()
    workspace = _workspace_provider()

    _stream(
        model,
        (workspace, knowledge),
        "结合我的记录和资料库的资料给我抽一题",
        with_home=True,
    )

    # Workspace first, then knowledge, then the answer: a multi-step retrieval
    # driven entirely by the descriptor the harness supplied.
    assert model.decisions == ["workspace", "knowledge", "answer"]
    assert _executed_names(workspace) == ["read_file"]
    assert _executed_names(knowledge) == ["search_knowledge"]


def test_without_the_source_descriptor_the_model_cannot_route(monkeypatch) -> None:
    # Guards the contract itself: if the descriptor ever stops reaching the
    # model, source routing silently degrades to a plain answer. Without this
    # the routing tests above could pass for the wrong reason.
    monkeypatch.setattr(
        agent_runtime,
        "render_capability_sources",
        lambda _config: "",
    )
    model = SourceAwareModel()
    knowledge = _knowledge_provider()
    workspace = _workspace_provider()

    _stream(
        model,
        (workspace, knowledge),
        "根据我的记录给我抽一题",
        with_home=True,
    )

    assert model.seen_source_labels == [[]]
    assert model.decisions == ["answer"]
    assert workspace.executed == []
    assert knowledge.executed == []


def test_one_sufficient_source_stops_the_loop_without_querying_the_other() -> None:
    call = ToolCall(id="c-1", name="search_knowledge", arguments={"value": "考纲"})
    model = ScriptedModel([(call,), ("基于资料库作答",)])
    knowledge = _knowledge_provider()
    workspace = _workspace_provider()

    events = _stream(model, (workspace, knowledge), "根据资料出一题")

    assert _executed_names(knowledge, workspace) == ["search_knowledge"]
    assert [type(event) for event in events] == [
        AssistantMessageEvent,
        ToolStartEvent,
        ToolResultEvent,
        TextDeltaEvent,
        AssistantMessageEvent,
    ]


def test_an_empty_source_lets_the_model_continue_with_the_other_source() -> None:
    empty = ToolCall(id="c-1", name="search_knowledge", arguments={"value": "考纲"})
    fallback = ToolCall(id="c-2", name="read_file", arguments={"value": "log.md"})
    model = ScriptedModel([(empty,), (fallback,), ("合并作答",)])
    knowledge = _knowledge_provider(
        {
            empty.id: ToolResult(
                call_id=empty.id,
                content=serialize_knowledge_result("rag", []),
                metadata={"tool": "search_knowledge", "status": "no_match"},
            )
        }
    )
    workspace = _workspace_provider()

    _stream(model, (workspace, knowledge), "根据我的记录出一题")

    # An empty knowledge result is a successful tool result, so the loop
    # continues instead of terminating on a failure.
    assert _executed_names(knowledge) == ["search_knowledge"]
    assert _executed_names(workspace) == ["read_file"]
    empty_result = json.loads(
        serialize_knowledge_result("rag", []),
    )
    assert empty_result["status"] == "no_match"


def test_an_explicit_two_source_request_completes_a_multi_step_retrieval() -> None:
    first = ToolCall(id="c-1", name="read_file", arguments={"value": "log.md"})
    second = ToolCall(id="c-2", name="search_knowledge", arguments={"value": "考纲"})
    model = ScriptedModel([(first,), (second,), ("综合两个来源作答",)])
    knowledge = _knowledge_provider()
    workspace = _workspace_provider()

    events = _stream(model, (workspace, knowledge), "结合我的记录和资料库出一题")

    assert _executed_names(workspace) == ["read_file"]
    assert _executed_names(knowledge) == ["search_knowledge"]
    assert [type(event) for event in events] == [
        AssistantMessageEvent,
        ToolStartEvent,
        ToolResultEvent,
        AssistantMessageEvent,
        ToolStartEvent,
        ToolResultEvent,
        TextDeltaEvent,
        AssistantMessageEvent,
    ]


def test_parallel_tool_calls_are_executed_and_correlated_by_call_id() -> None:
    first = ToolCall(id="c-1", name="read_file", arguments={"value": "log.md"})
    second = ToolCall(id="c-2", name="search_knowledge", arguments={"value": "考纲"})
    model = ScriptedModel([(first, second), ("综合作答",)])
    knowledge = _knowledge_provider()
    workspace = _workspace_provider()

    events = _stream(model, (workspace, knowledge), "同时查两个来源")

    assistant = events[0]
    assert isinstance(assistant, AssistantMessageEvent)
    assert assistant.tool_calls == (first, second)
    starts = [event for event in events if isinstance(event, ToolStartEvent)]
    results = [event for event in events if isinstance(event, ToolResultEvent)]
    assert [start.call.id for start in starts] == ["c-1", "c-2"]
    # Each result carries the id of the call it answers, regardless of the order
    # in which the tool node completed them.
    for result in results:
        assert result.result.call_id == result.call.id
    assert {result.call.id for result in results} == {"c-1", "c-2"}
    assert _executed_names(workspace) == ["read_file"]
    assert _executed_names(knowledge) == ["search_knowledge"]


def test_one_parallel_turn_counts_as_a_single_tool_round() -> None:
    # A turn with N parallel calls emits N ToolMessages. Counting those as
    # rounds would let one parallel turn exhaust the limit and block the final
    # answer, so rounds are counted per assistant turn instead.
    first = ToolCall(id="c-1", name="read_file", arguments={"value": "a.md"})
    second = ToolCall(id="c-2", name="search_knowledge", arguments={"value": "b"})
    model = ScriptedModel([(first, second), ("综合作答",)])
    knowledge = _knowledge_provider()
    workspace = _workspace_provider()

    events = _stream(
        model,
        (workspace, knowledge),
        "同时查两个来源",
        max_tool_rounds=2,
    )

    assert isinstance(events[-1], AssistantMessageEvent)
    assert _executed_names(workspace, knowledge) == [
        "read_file",
        "search_knowledge",
    ]


def test_the_tool_round_limit_still_stops_a_looping_agent() -> None:
    looping = ToolCall(id="c-1", name="read_file", arguments={"value": "a.md"})
    model = ScriptedModel(
        [
            (replace(looping, id=f"c-{index}"),)
            for index in range(1, 6)
        ]
    )
    workspace = _workspace_provider()

    with pytest.raises(RuntimeError, match="tool_call_limit_exceeded"):
        _stream(model, (workspace,), "一直查", max_tool_rounds=2)

    assert len(workspace.executed) == 2


def test_parallel_calls_split_the_remaining_result_budget() -> None:
    first = ToolCall(id="c-1", name="read_file", arguments={"value": "a.md"})
    second = ToolCall(id="c-2", name="search_knowledge", arguments={"value": "b"})
    budgets: dict[str, int] = {}

    class BudgetRecordingProvider(RecordingCapabilityProvider):
        def execute(
            self,
            call: ToolCall,
            context: CapabilityContext,
        ) -> ToolResult:
            budgets[call.id] = context.token_budget
            return super().execute(call, context)

    workspace = BudgetRecordingProvider(
        module="agent_home",
        definitions=(_tool("read_file", "Read one file."),),
    )
    knowledge = BudgetRecordingProvider(
        module="knowledge_base",
        definitions=(_tool("search_knowledge", "Search knowledge."),),
    )
    model = ScriptedModel([(first, second), ("综合作答",)])

    _stream(model, (workspace, knowledge), "同时查两个来源", token_budget=40_000)

    # Siblings share one turn's state, so each must receive a share rather than
    # the whole remaining budget; otherwise both fit alone and only the merged
    # state is found to be over budget.
    assert set(budgets) == {"c-1", "c-2"}
    assert budgets["c-1"] == budgets["c-2"]
    assert sum(budgets.values()) <= 40_000


def test_a_single_call_still_receives_the_whole_remaining_budget() -> None:
    only = ToolCall(id="c-1", name="read_file", arguments={"value": "a.md"})
    budgets: dict[str, int] = {}

    class BudgetRecordingProvider(RecordingCapabilityProvider):
        def execute(
            self,
            call: ToolCall,
            context: CapabilityContext,
        ) -> ToolResult:
            budgets[call.id] = context.token_budget
            return super().execute(call, context)

    workspace = BudgetRecordingProvider(
        module="agent_home",
        definitions=(_tool("read_file", "Read one file."),),
    )
    model = ScriptedModel([(only,), ("作答",)])

    _stream(model, (workspace,), "查一个来源", token_budget=40_000)

    # The split must not shrink the ordinary single-call path.
    assert budgets["c-1"] > 30_000


def test_parallel_and_sequential_tool_calls_persist_as_a_valid_message_chain() -> None:
    parallel_one = ToolCall(id="c-1", name="read_file", arguments={"value": "a.md"})
    parallel_two = ToolCall(
        id="c-2",
        name="search_knowledge",
        arguments={"value": "考纲"},
    )
    follow_up = ToolCall(id="c-3", name="list_files", arguments={"value": ""})
    model = ScriptedModel(
        [(parallel_one, parallel_two), (follow_up,), ("最终回答",)]
    )
    knowledge = _knowledge_provider()
    workspace = _workspace_provider()

    events = _stream(model, (workspace, knowledge), "查两个来源后再补一次")

    reducer = RuntimeEventReducer(provider="qwen", model="qwen3.7-plus")
    for event in events:
        reducer.accept(event)
    result = reducer.finish_success()
    chain = result["messages_chain"]
    assert isinstance(chain, list)

    declared: list[str] = []
    resolved: list[str] = []
    for message in chain:
        if message["role"] == "assistant":
            declared.extend(
                call["id"] for call in message.get("tool_calls", [])
            )
        else:
            resolved.append(message["tool_call_id"])
    assert declared == ["c-1", "c-2", "c-3"]
    # Parallel calls may resolve in either order, so the chain is correlated by
    # call id rather than by position: the parallel pair resolves before the
    # follow-up round, and validate_messages_chain already rejected any orphan,
    # duplicate or unresolved id.
    assert set(resolved[:2]) == {"c-1", "c-2"}
    assert resolved[2] == "c-3"
    # The first assistant message carries both parallel calls in one turn.
    assert len(chain[0]["tool_calls"]) == 2
    assert result["value"] == "最终回答"


def test_a_reused_tool_call_id_is_reported_as_a_runtime_protocol_violation() -> None:
    duplicate = ToolCall(id="c-1", name="read_file", arguments={"value": "a.md"})
    other = ToolCall(id="c-1", name="list_files", arguments={"value": ""})
    model = ScriptedModel([(duplicate, other), ("unused",)])
    workspace = _workspace_provider()

    with pytest.raises(RuntimeTerminalError) as captured:
        _stream(model, (workspace,), "查两个来源")

    assert captured.value.code == "runtime_tool_protocol_violation"
    assert _safe_execution_error_code(captured.value) == (
        "runtime_tool_protocol_violation"
    )


@pytest.mark.parametrize(
    ("arguments", "expected_code"),
    [
        ("not-json", "provider_tool_call_invalid"),
        ("[1,2]", "provider_tool_call_invalid"),
        (None, "provider_tool_call_invalid"),
    ],
)
def test_a_malformed_provider_tool_call_stream_is_diagnosable(
    tmp_path,
    arguments: str | None,
    expected_code: str,
) -> None:
    from types import SimpleNamespace

    chunk = SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=0,
                            id="c-1",
                            type="function",
                            function=SimpleNamespace(
                                name="search_knowledge",
                                arguments=arguments,
                            ),
                        )
                    ],
                )
            )
        ]
    )

    class Completions:
        def create(self, **_kwargs: object) -> object:
            return iter([chunk])

    class Client:
        def __init__(self) -> None:
            self.chat = SimpleNamespace(completions=Completions())

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        openai_api_key="provider-secret",
        deepseek_api_key=None,
        qwen_api_key=None,
    )
    service = ModelService(
        settings=settings,
        client_factory=lambda **_kwargs: Client(),
    )

    with pytest.raises(HTTPException) as captured:
        list(
            service.stream_turn(
                [{"role": "user", "content": "hello"}],
                provider="openai",
                model="gpt-4.1-mini",
                call_index=1,
                observer=_ignore_provider_metrics,
            )
        )

    assert captured.value.detail["code"] == expected_code
    assert _safe_execution_error_code(captured.value) == expected_code
    # A model protocol fault must never masquerade as an unreachable provider.
    assert _safe_execution_error_code(captured.value) != "provider_call_failed"


def _knowledge_context(
    factory: RecordingSessionFactory,
    *,
    with_knowledge: bool = True,
    token_budget: int = 4_321,
) -> CapabilityContext:
    """A capability context for the real Knowledge provider."""

    config = _agent_config()
    if not with_knowledge:
        config = replace(config, knowledge_scopes=())
    return CapabilityContext(
        user_id=7,
        agent_config=config,
        session_factory=factory,  # type: ignore[arg-type]
        token_budget=token_budget,
    )


def _knowledge_capability(
    *,
    result: KnowledgeSearchResult | None = None,
    retrieval_error: Exception | None = None,
) -> tuple[KnowledgeCapabilityProvider, RecordingSessionFactory]:
    scope_service = RecordingScopeService([])
    retrieval = RecordingRetrieval(
        result
        or KnowledgeSearchResult(
            mode="rag",
            sources=[],
            content=serialize_knowledge_result("rag", []),
        )
    )
    retrieval.scope_service = scope_service
    retrieval.error = retrieval_error
    provider = KnowledgeCapabilityProvider(
        scope_service=scope_service,  # type: ignore[arg-type]
        retrieval=retrieval,  # type: ignore[arg-type]
    )
    return provider, RecordingSessionFactory()


def test_an_empty_knowledge_result_is_a_successful_no_match_result() -> None:
    provider, factory = _knowledge_capability()

    result = provider.execute(
        ToolCall(id="c-1", name="search_knowledge", arguments={"query": "考纲"}),
        _knowledge_context(factory),
    )

    assert result.is_error is False
    assert result.metadata["status"] == "no_match"
    payload = json.loads(result.content)
    assert payload["status"] == "no_match"
    assert payload["sources"] == []
    assert "code" not in payload


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            HTTPException(
                status_code=503,
                detail={
                    "code": "knowledge_retriever_unavailable",
                    "message": "internal retriever endpoint",
                },
            ),
            "knowledge_retriever_unavailable",
        ),
        (
            HTTPException(
                status_code=503,
                detail={
                    "code": "knowledge_content_unavailable",
                    "message": "internal object store endpoint",
                },
            ),
            "knowledge_content_unavailable",
        ),
    ],
)
def test_a_knowledge_system_failure_never_looks_like_an_empty_result(
    error: HTTPException,
    expected_code: str,
) -> None:
    provider, factory = _knowledge_capability(retrieval_error=error)

    result = provider.execute(
        ToolCall(id="c-1", name="search_knowledge", arguments={"query": "考纲"}),
        _knowledge_context(factory),
    )

    assert result.is_error is True
    payload = json.loads(result.content)
    assert payload["code"] == expected_code
    assert "status" not in payload
    assert "sources" not in payload
    assert "internal" not in result.content


def test_a_knowledge_scope_failure_is_reported_apart_from_retrieval() -> None:
    scope_service = RecordingScopeService([])
    scope_service.error = HTTPException(
        status_code=503,
        detail={
            "code": "knowledge_unavailable",
            "message": "internal scope detail",
        },
    )
    retrieval = RecordingRetrieval(
        KnowledgeSearchResult(
            mode="rag",
            sources=[],
            content=serialize_knowledge_result("rag", []),
        )
    )
    retrieval.scope_service = scope_service
    provider = KnowledgeCapabilityProvider(
        scope_service=scope_service,  # type: ignore[arg-type]
        retrieval=retrieval,  # type: ignore[arg-type]
    )

    result = provider.execute(
        ToolCall(id="c-1", name="search_knowledge", arguments={"query": "考纲"}),
        _knowledge_context(RecordingSessionFactory()),
    )

    assert result.is_error is True
    assert json.loads(result.content)["code"] == "knowledge_scope_unavailable"
    assert retrieval.calls == []


def test_an_invalid_knowledge_argument_is_a_parameter_error() -> None:
    provider, factory = _knowledge_capability()

    result = provider.execute(
        ToolCall(id="c-1", name="search_knowledge", arguments={"query": ""}),
        _knowledge_context(factory),
    )

    assert result.is_error is True
    assert json.loads(result.content)["code"] == "knowledge_request_invalid"


def test_an_unbound_tool_is_reported_as_unavailable_not_as_an_empty_result() -> None:
    provider, factory = _knowledge_capability()

    result = provider.execute(
        ToolCall(id="c-1", name="search_knowledge", arguments={"query": "考纲"}),
        _knowledge_context(factory, with_knowledge=False),
    )

    assert result.is_error is True
    assert json.loads(result.content)["code"] == "tool_not_found"
