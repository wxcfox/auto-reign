from __future__ import annotations

from app.db import models
from app.services.message_chain import validate_messages_chain


class SubtaskHistoryProjector:
    """Safely turns persisted Subtask data into UI/runtime history messages."""

    def project_user(
        self,
        subtask: models.Subtask | None = None,
        *,
        prompt: str | None = None,
    ) -> list[dict[str, object]]:
        value = subtask.prompt if subtask is not None else prompt
        return [{"role": "user", "content": value or ""}]

    def project_assistant(
        self,
        subtask: models.Subtask | None = None,
        *,
        status: str | None = None,
        result: object = None,
    ) -> list[dict[str, object]]:
        effective_status = subtask.status if subtask is not None else status
        stored_result = subtask.result if subtask is not None else result
        values = stored_result if isinstance(stored_result, dict) else {}
        value = values.get("value")

        # In-flight and failed histories cannot safely be replayed. Cancelled
        # work exposes at most its plain partial value, never tool state.
        if effective_status in {"PENDING", "RUNNING"}:
            return []
        if effective_status in {"FAILED", "CANCELLED"}:
            return self._value_message(value)

        chain = values.get("messages_chain")
        if effective_status == "COMPLETED":
            try:
                return validate_messages_chain(chain)
            except ValueError:
                pass
        return self._value_message(value)

    @staticmethod
    def _value_message(value: object) -> list[dict[str, object]]:
        if isinstance(value, str) and value:
            return [{"role": "assistant", "content": value}]
        return []
