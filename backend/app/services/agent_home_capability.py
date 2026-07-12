from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import json

from pydantic import BaseModel, ConfigDict, ValidationError

from app.services.agent_home_paths import normalize_home_path
from app.services.agent_home_service import (
    AgentHomeService,
    WorkspaceConflict,
    WorkspaceFileNotUtf8,
    WorkspaceUnavailable,
)
from app.services.runtime_types import (
    CapabilityContext,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from app.services.token_counter import RuntimeTokenCounter
from app.storage.object_store import ObjectNotFound, ObjectStoreError


class ListFilesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory: str = ""


class ReadFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str


class CreateFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str


class WriteFileInput(CreateFileInput):
    model_config = ConfigDict(extra="forbid")

    expected_etag: str


TOOL_MODELS: tuple[tuple[str, str, type[BaseModel]], ...] = (
    (
        "list_files",
        "List direct children in one Agent Home directory.",
        ListFilesInput,
    ),
    (
        "read_file",
        "Read one UTF-8 Agent Home file and its ETag.",
        ReadFileInput,
    ),
    (
        "create_file",
        "Create a new UTF-8 Agent Home file; fail if it exists.",
        CreateFileInput,
    ),
    (
        "write_file",
        "Replace one Agent Home file using the ETag returned by read_file.",
        WriteFileInput,
    ),
)


def _json_value(value: object) -> object:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported tool result type: {type(value).__name__}")


def path_sha256(path: str) -> str:
    """Return an audit-only digest without retaining a user path or body."""
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


class ToolResultBudgetExceeded(Exception):
    pass


def mutation_success_content(*, path: str, etag: str, size_bytes: int) -> str:
    return json.dumps(
        {"path": path, "etag": etag, "size_bytes": size_bytes},
        ensure_ascii=False,
        separators=(",", ":"),
    )


class AgentHomeCapabilityProvider:
    def __init__(
        self,
        *,
        service: AgentHomeService,
        token_counter: RuntimeTokenCounter,
    ) -> None:
        self.service = service
        self.token_counter = token_counter

    def prompt_modules(self, context: CapabilityContext) -> tuple[str, ...]:
        if context.agent_config.home_workspace is None:
            return ()
        return ("agent_home",)

    def tool_definitions(
        self,
        context: CapabilityContext,
    ) -> tuple[ToolDefinition, ...]:
        if context.agent_config.home_workspace is None:
            return ()
        return tuple(
            ToolDefinition(
                name=name,
                description=description,
                input_schema=model.model_json_schema(),
            )
            for name, description, model in TOOL_MODELS
        )

    def execute(self, call: ToolCall, context: CapabilityContext) -> ToolResult:
        home = context.agent_config.home_workspace
        if home is None:
            return self._error(
                call,
                "workspace_unavailable",
                "This Agent has no Agent Home.",
            )
        try:
            if call.name == "list_files":
                payload = ListFilesInput.model_validate(call.arguments)
                result = self.service.list_files(
                    user_id=context.user_id,
                    workspace_id=home.workspace_id,
                    directory=payload.directory,
                )
                content = json.dumps(
                    _json_value(result),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                metadata = {
                    "tool": call.name,
                    "path_sha256": path_sha256(payload.directory or "."),
                }
            elif call.name == "read_file":
                payload = ReadFileInput.model_validate(call.arguments)
                result = self.service.read_file(
                    user_id=context.user_id,
                    workspace_id=home.workspace_id,
                    path=payload.path,
                )
                content = json.dumps(
                    _json_value(result),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                metadata = {
                    "tool": call.name,
                    "path_sha256": path_sha256(result.path),
                    "etag": result.etag,
                }
            elif call.name == "create_file":
                payload = CreateFileInput.model_validate(call.arguments)
                normalized = normalize_home_path(payload.path)
                data = self.service.validate_content(payload.content)
                self._require_mutation_result_budget(
                    call=call,
                    context=context,
                    path=normalized,
                    size_bytes=len(data),
                )
                result = self.service.create_file(
                    user_id=context.user_id,
                    workspace_id=home.workspace_id,
                    path=normalized,
                    content=payload.content,
                )
                content = mutation_success_content(
                    path=result.path,
                    etag=result.etag,
                    size_bytes=result.size_bytes,
                )
                metadata = {
                    "tool": call.name,
                    "path_sha256": path_sha256(result.path),
                    "etag": result.etag,
                }
            elif call.name == "write_file":
                payload = WriteFileInput.model_validate(call.arguments)
                normalized = normalize_home_path(payload.path)
                data = self.service.validate_content(payload.content)
                self._require_mutation_result_budget(
                    call=call,
                    context=context,
                    path=normalized,
                    size_bytes=len(data),
                )
                result = self.service.write_file(
                    user_id=context.user_id,
                    workspace_id=home.workspace_id,
                    path=normalized,
                    content=payload.content,
                    expected_etag=payload.expected_etag,
                )
                content = mutation_success_content(
                    path=result.path,
                    etag=result.etag,
                    size_bytes=result.size_bytes,
                )
                metadata = {
                    "tool": call.name,
                    "path_sha256": path_sha256(result.path),
                    "etag": result.etag,
                }
            else:
                return self._error(
                    call,
                    "tool_not_found",
                    "The requested tool is unavailable.",
                )

            if (
                call.name in {"read_file", "list_files"}
                and self.token_counter.count_tool_result(
                    call_id=call.id,
                    content=content,
                )
                > context.token_budget
            ):
                return self._error(
                    call,
                    "context_too_large",
                    "The tool result exceeds the remaining context budget.",
                )
            return ToolResult(
                call_id=call.id,
                content=content,
                metadata=metadata,
            )
        except WorkspaceConflict:
            return self._error(
                call,
                "workspace_conflict",
                "The workspace file changed. Read it again before writing.",
            )
        except ObjectNotFound:
            return self._error(
                call,
                "workspace_file_not_found",
                "The workspace file was not found.",
            )
        except (WorkspaceUnavailable, WorkspaceFileNotUtf8, ObjectStoreError):
            return self._error(
                call,
                "workspace_unavailable",
                "The workspace is temporarily unavailable.",
            )
        except ToolResultBudgetExceeded:
            return self._error(
                call,
                "context_too_large",
                "The tool result exceeds the remaining context budget.",
            )
        except (ValueError, ValidationError):
            return self._error(
                call,
                "workspace_request_invalid",
                "The workspace tool arguments are invalid.",
            )

    def _require_mutation_result_budget(
        self,
        *,
        call: ToolCall,
        context: CapabilityContext,
        path: str,
        size_bytes: int,
    ) -> None:
        maximum_envelope = mutation_success_content(
            path=path,
            etag="\x00" * 256,
            size_bytes=size_bytes,
        )
        if (
            self.token_counter.count_tool_result(
                call_id=call.id,
                content=maximum_envelope,
            )
            > context.token_budget
        ):
            raise ToolResultBudgetExceeded()

    @staticmethod
    def _error(call: ToolCall, code: str, message: str) -> ToolResult:
        return ToolResult(
            call_id=call.id,
            content=json.dumps(
                {"code": code, "message": message},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            is_error=True,
            metadata={"tool": call.name, "code": code},
        )
