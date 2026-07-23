"""Stable public API for ephemeral chat stream state."""

from app.services.chat_stream_builder import ChatRealtimeBackend, build_chat_realtime
from app.services.chat_stream_memory import MemoryChatStreamStore
from app.services.chat_stream_redis import RedisChatStreamStore
from app.services.chat_stream_types import (
    ActiveStreamSnapshot,
    ChatStreamError,
    ChatStreamMalformedState,
    ChatStreamNotActive,
    ChatStreamOffsetMismatch,
    ChatStreamStaleGeneration,
    ChatStreamStore,
)

__all__ = [
    "ActiveStreamSnapshot",
    "ChatRealtimeBackend",
    "ChatStreamError",
    "ChatStreamMalformedState",
    "ChatStreamNotActive",
    "ChatStreamOffsetMismatch",
    "ChatStreamStaleGeneration",
    "ChatStreamStore",
    "MemoryChatStreamStore",
    "RedisChatStreamStore",
    "build_chat_realtime",
]
