from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Literal

from redis.asyncio import Redis

from app.core.config import Settings
from app.services.chat_stream_memory import MemoryChatStreamStore
from app.services.chat_stream_redis import RedisChatStreamStore
from app.services.chat_stream_types import ChatStreamStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatRealtimeBackend:
    stream_store: ChatStreamStore
    redis_available: bool
    backend: Literal["redis", "memory"]
    degraded: bool
    redis_client: Redis | None = None

    async def aclose(self) -> None:
        await self.stream_store.aclose()


async def build_chat_realtime(settings: Settings) -> ChatRealtimeBackend:
    client: Redis | None = None
    try:
        client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
        )
        await client.ping()
        store = RedisChatStreamStore(
            client,
            ttl_seconds=settings.chat_stream_ttl_seconds,
            key_prefix=settings.chat_stream_key_prefix,
            owns_client=True,
        )
    except asyncio.CancelledError:
        if client is not None:
            await _close_redis_client(client)
        raise
    except Exception as exc:
        if client is not None:
            await _close_redis_client(client)
        logger.warning(
            "Redis chat realtime setup failed; using in-process memory state",
            extra={"error_type": type(exc).__name__},
        )
        return ChatRealtimeBackend(
            stream_store=MemoryChatStreamStore(
                ttl_seconds=settings.chat_stream_ttl_seconds,
            ),
            redis_available=False,
            backend="memory",
            degraded=True,
        )
    return ChatRealtimeBackend(
        stream_store=store,
        redis_available=True,
        backend="redis",
        degraded=False,
        redis_client=client,
    )


async def _close_redis_client(client: Redis) -> None:
    close_task = asyncio.create_task(client.aclose())
    try:
        await asyncio.shield(close_task)
    except asyncio.CancelledError:
        try:
            await close_task
        except Exception:
            logger.warning("Failed to close Redis client after cancelled setup")
    except Exception:
        logger.warning("Failed to close Redis client after chat realtime setup failure")
