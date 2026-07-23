from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import os
import re
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis as AsyncRedis

from app.core.config import Settings
from app.core.limits import MAX_CHAT_BLOCK_ID_LENGTH
from app.services.chat_blocks import create_text_block, create_tool_block, update_tool_block
from app.services.chat_stream_store import (
    ChatStreamMalformedState,
    ChatStreamNotActive,
    ChatStreamOffsetMismatch,
    ChatStreamStaleGeneration,
    MemoryChatStreamStore,
    RedisChatStreamStore,
    build_chat_realtime,
)
from app.services.json_safety import canonical_json
from app.services.runtime_types import ToolCall
from app.services.text_offsets import utf16_code_units


_FAKE_BLOCK_ID_PATTERN = re.compile(
    rf"[A-Za-z0-9._:-]{{1,{MAX_CHAT_BLOCK_ID_LENGTH}}}",
    flags=re.ASCII,
)


class FakeRedis:
    def __init__(self, *, ping_error: Exception | None = None) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.expire_calls: dict[str, int] = {}
        self.closed = False
        self.close_calls = 0
        self.ping_error = ping_error
        self._lock = asyncio.Lock()

    async def ping(self) -> bool:
        if self.ping_error is not None:
            raise self.ping_error
        return True

    async def aclose(self) -> None:
        self.closed = True
        self.close_calls += 1

    async def hgetall(self, key: str) -> dict[str, str]:
        return deepcopy(self.hashes.get(key, {}))

    async def hset(self, key: str, mapping: dict[str, str]) -> None:
        self.hashes.setdefault(key, {}).update(mapping)

    async def eval(self, script: str, numkeys: int, *values: str) -> object:
        keys = values[:numkeys]
        args = values[numkeys:]
        async with self._lock:
            if script.startswith("-- chat_stream:start"):
                task_key, subtask_key = keys
                prefix, subtask_id, _ttl, task_id, now, generation_id = args
                old = self.hashes.get(task_key, {}).get("subtask_id")
                if old is not None and old != subtask_id:
                    old_stream = self.hashes.get(f"{prefix}:subtask:{old}:stream")
                    if (
                        old_stream is None
                        or old_stream.get("task_id") != task_id
                        or old_stream.get("subtask_id") != old
                    ):
                        return ["malformed"]
                    self.hashes.pop(f"{prefix}:subtask:{old}:stream", None)
                old_task = self.hashes.get(subtask_key, {}).get("task_id")
                if old_task is not None and old_task != task_id:
                    old_task_key = f"{prefix}:task:{old_task}:active"
                    if self.hashes.get(old_task_key, {}).get("subtask_id") == subtask_id:
                        self.hashes.pop(old_task_key, None)
                self.hashes[task_key] = {"subtask_id": subtask_id}
                self.hashes[subtask_key] = {
                    "task_id": task_id,
                    "subtask_id": subtask_id,
                    "offset": "0",
                    "cached_content": "",
                    "block_order": "[]",
                    "started_at": now,
                    "last_activity_at": now,
                    "cancelled": "0",
                    "generation_id": generation_id,
                }
                self._expire(task_key)
                self._expire(subtask_key)
                return ["ok"]
            if script.startswith("-- chat_stream:append"):
                (subtask_key,) = keys
                (
                    offset,
                    content,
                    new_offset,
                    now,
                    _ttl,
                    prefix,
                    generation_id,
                    content_units,
                    _max_bytes,
                ) = args
                state = self.hashes.get(subtask_key)
                if state is None:
                    return ["not_active"]
                if "offset" not in state or "task_id" not in state:
                    return ["malformed"]
                if state.get("generation_id") != generation_id:
                    return ["stale"]
                current = state["offset"]
                if utf16_code_units(state.get("cached_content", "")) != int(current):
                    return ["malformed"]
                if utf16_code_units(content) != int(content_units):
                    return ["malformed"]
                if current != offset:
                    return ["offset"]
                if int(new_offset) != int(current) + int(content_units):
                    return ["malformed"]
                task_key = f"{prefix}:task:{state['task_id']}:active"
                if self.hashes.get(task_key, {}).get("subtask_id") != state["subtask_id"]:
                    return ["not_active"]
                state["cached_content"] = state.get("cached_content", "") + content
                state["offset"] = new_offset
                state["last_activity_at"] = now
                self._expire(task_key)
                self._expire(subtask_key)
                return ["ok", new_offset]
            if script.startswith("-- chat_stream:snapshot"):
                (task_key,) = keys
                prefix, task_id, _max_id, _max_bytes = args
                pointer = self.hashes.get(task_key)
                if pointer is None:
                    return ["none"]
                subtask_id = pointer.get("subtask_id")
                state = self.hashes.get(f"{prefix}:subtask:{subtask_id}:stream")
                if state is None or state.get("task_id") != task_id:
                    return ["malformed"]
                try:
                    if utf16_code_units(state["cached_content"]) != int(state["offset"]):
                        return ["malformed"]
                    order = json.loads(state["block_order"])
                    blocks = [state[f"block:{block_id}"] for block_id in order]
                    response = [
                        "ok",
                        *(state[key] for key in (
                            "task_id",
                            "subtask_id",
                            "generation_id",
                            "offset",
                            "cached_content",
                            "block_order",
                            "started_at",
                            "last_activity_at",
                            "cancelled",
                        )),
                        state.get("status_updated", ""),
                        *blocks,
                    ]
                except (KeyError, TypeError, ValueError):
                    return ["malformed"]
                return response
            if script.startswith("-- chat_stream:upsert"):
                (subtask_key,) = keys
                (
                    block_id,
                    block_json,
                    now,
                    _ttl,
                    _unused,
                    prefix,
                    generation_id,
                    max_block_id_length,
                ) = args
                state = self.hashes.get(subtask_key)
                if state is None:
                    return ["not_active"]
                if state.get("generation_id") != generation_id:
                    return ["stale"]
                task_id = state.get("task_id")
                subtask_id = state.get("subtask_id")
                task_key = f"{prefix}:task:{task_id}:active"
                if self.hashes.get(task_key, {}).get("subtask_id") != subtask_id:
                    return ["not_active"]
                try:
                    order = json.loads(state["block_order"])
                except Exception:
                    return ["malformed"]
                if (
                    not isinstance(order, list)
                    or int(max_block_id_length) != MAX_CHAT_BLOCK_ID_LENGTH
                    or any(
                        not isinstance(item, str)
                        or _FAKE_BLOCK_ID_PATTERN.fullmatch(item) is None
                        for item in order
                    )
                    or len(set(order)) != len(order)
                ):
                    return ["malformed"]
                if block_id not in order:
                    order.append(block_id)
                state["block_order"] = json.dumps(order, separators=(",", ":"))
                state[f"block:{block_id}"] = block_json
                state["last_activity_at"] = now
                self._expire(task_key)
                self._expire(subtask_key)
                return ["ok"]
            if script.startswith("-- chat_stream:mutate"):
                (subtask_key,) = keys
                field, value, now, _ttl, _unused, prefix, generation_id = args
                state = self.hashes.get(subtask_key)
                if state is None:
                    return ["not_active"]
                if state.get("generation_id") != generation_id:
                    return ["stale"]
                task_key = f"{prefix}:task:{state.get('task_id')}:active"
                if self.hashes.get(task_key, {}).get("subtask_id") != state.get("subtask_id"):
                    return ["not_active"]
                state[field] = value
                state["last_activity_at"] = now
                self._expire(task_key)
                self._expire(subtask_key)
                return ["ok"]
            if script.startswith("-- chat_stream:cancelled"):
                (subtask_key,) = keys
                generation_id, _prefix = args
                state = self.hashes.get(subtask_key)
                if state is None:
                    return ["none"]
                if state.get("generation_id") != generation_id:
                    return ["stale"]
                if state.get("cancelled") not in {"0", "1"}:
                    return ["malformed"]
                return ["ok", state["cancelled"]]
            if script.startswith("-- chat_stream:finalize"):
                task_key, subtask_key = keys
                subtask_id, task_id, generation_id = args
                if self.hashes.get(task_key, {}).get("subtask_id") != subtask_id:
                    return ["stale"]
                state = self.hashes.get(subtask_key, {})
                if state.get("task_id") != task_id or state.get("subtask_id") != subtask_id:
                    return ["stale"]
                if state.get("generation_id") != generation_id:
                    return ["stale"]
                self.hashes.pop(task_key, None)
                self.hashes.pop(subtask_key, None)
                return ["ok"]
            if script.startswith("-- chat_stream:validate"):
                task_key, subtask_key = keys
                subtask_id, task_id, generation_id = args
                if task_key not in self.hashes or subtask_key not in self.hashes:
                    return ["not_active"]
                if self.hashes.get(task_key, {}).get("subtask_id") != subtask_id:
                    return ["stale"]
                state = self.hashes[subtask_key]
                if (
                    state.get("task_id") != task_id
                    or state.get("subtask_id") != subtask_id
                    or state.get("generation_id") != generation_id
                ):
                    return ["stale"]
                return ["ok"]
        raise AssertionError("unknown script")

    def _expire(self, key: str) -> None:
        self.expire_calls[key] = self.expire_calls.get(key, 0) + 1


class BlockingPingRedis(FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.ping_started = asyncio.Event()

    async def ping(self) -> bool:
        self.ping_started.set()
        await asyncio.Event().wait()
        return True


StoreFactory = Callable[[], MemoryChatStreamStore | RedisChatStreamStore]


def _factories() -> list[object]:
    return [
        pytest.param(lambda: MemoryChatStreamStore(), id="memory"),
        pytest.param(
            lambda: RedisChatStreamStore(FakeRedis(), key_prefix="test:chat"),
            id="redis",
        ),
    ]


@pytest.mark.parametrize("factory", _factories())
def test_stream_store_contract_round_trip_order_copies_and_finalize(
    factory: StoreFactory,
) -> None:
    async def scenario() -> None:
        store = factory()
        generation_id = await store.start(task_id=3, subtask_id=9)
        assert UUID(generation_id).version == 4
        assert (
            await store.append_text(
                subtask_id=9,
                generation_id=generation_id,
                block_id="text-1",
                offset=0,
                content="你好",
            )
            == 2
        )
        first = create_text_block("你好", block_id="text-1")
        second = create_tool_block(
            ToolCall(id="call-1", name="read_file", arguments={}),
            block_id="tool-1",
        )
        await store.upsert_block(subtask_id=9, generation_id=generation_id, block=first)
        await store.upsert_block(subtask_id=9, generation_id=generation_id, block=second)
        second_done = update_tool_block(second, status="done", tool_output={"ok": True})
        await store.upsert_block(subtask_id=9, generation_id=generation_id, block=second_done)
        status = {"status": "running", "nested": {"step": 1}}
        await store.set_status_snapshot(subtask_id=9, generation_id=generation_id, payload=status)
        await store.set_cancelled(subtask_id=9, generation_id=generation_id)

        snapshot = await store.get_active(task_id=3)
        assert snapshot is not None
        assert snapshot.task_id == 3
        assert snapshot.subtask_id == 9
        assert snapshot.generation_id == generation_id
        assert snapshot.offset == 2
        assert snapshot.cached_content == "你好"
        assert [block["id"] for block in snapshot.blocks] == ["text-1", "tool-1"]
        assert snapshot.blocks[1]["status"] == "done"
        assert snapshot.started_at.endswith("Z")
        assert snapshot.last_activity_at.endswith("Z")
        assert snapshot.status_updated == status
        await store.validate_generation(
            task_id=3,
            subtask_id=9,
            generation_id=generation_id,
        )
        assert await store.is_cancelled(subtask_id=9, generation_id=generation_id) is True

        status["nested"]["step"] = 2  # type: ignore[index]
        snapshot.blocks[0]["content"] = "changed"
        assert (await store.get_active(task_id=3)).status_updated == {
            "nested": {"step": 1},
            "status": "running",
        }
        assert (await store.get_active(task_id=3)).blocks[0]["content"] == "你好"

        await store.finalize(task_id=3, subtask_id=9, generation_id=generation_id)
        assert await store.get_active(task_id=3) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("factory", _factories())
def test_stream_store_contract_rejects_offsets_and_has_one_concurrent_winner(
    factory: StoreFactory,
) -> None:
    async def scenario() -> None:
        store = factory()
        generation_id = await store.start(task_id=1, subtask_id=2)
        with pytest.raises(ChatStreamOffsetMismatch, match="^chat_stream_offset_mismatch$"):
            await store.append_text(
                subtask_id=2,
                generation_id=generation_id,
                block_id="text-1",
                offset=1,
                content="bad",
            )
        results = await asyncio.gather(
            store.append_text(
                subtask_id=2,
                generation_id=generation_id,
                block_id="text-1",
                offset=0,
                content="a",
            ),
            store.append_text(
                subtask_id=2,
                generation_id=generation_id,
                block_id="text-1",
                offset=0,
                content="b",
            ),
            return_exceptions=True,
        )
        assert sum(result == 1 for result in results) == 1
        assert sum(isinstance(result, ChatStreamOffsetMismatch) for result in results) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("factory", _factories())
def test_stream_store_contract_stale_finalize_cannot_remove_replacement(
    factory: StoreFactory,
) -> None:
    async def scenario() -> None:
        store = factory()
        old_generation = await store.start(task_id=4, subtask_id=10)
        await store.start(task_id=4, subtask_id=11)
        await store.finalize(task_id=4, subtask_id=10, generation_id=old_generation)
        active = await store.get_active(task_id=4)
        assert active is not None and active.subtask_id == 11
        with pytest.raises(ChatStreamNotActive, match="^chat_stream_not_active$"):
            await store.validate_generation(
                task_id=4,
                subtask_id=10,
                generation_id=old_generation,
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("factory", _factories())
def test_stream_store_contract_reusing_subtask_clears_previous_task_pointer(
    factory: StoreFactory,
) -> None:
    async def scenario() -> None:
        store = factory()
        await store.start(task_id=4, subtask_id=10)
        await store.start(task_id=5, subtask_id=10)

        assert await store.get_active(task_id=4) is None
        active = await store.get_active(task_id=5)
        assert active is not None and active.subtask_id == 10

    asyncio.run(scenario())


def test_redis_start_rejects_cross_task_pointer_without_deleting_owner() -> None:
    client = FakeRedis()
    store = RedisChatStreamStore(client, key_prefix="ownership:test")

    async def scenario() -> None:
        await store.start(task_id=4, subtask_id=10)
        await store.start(task_id=5, subtask_id=11)
        client.hashes["ownership:test:task:4:active"]["subtask_id"] = "11"
        owned = deepcopy(client.hashes["ownership:test:subtask:11:stream"])

        with pytest.raises(
            ChatStreamMalformedState,
            match="^chat_stream_malformed_state$",
        ):
            await store.start(task_id=4, subtask_id=12)

        assert client.hashes["ownership:test:task:4:active"] == {
            "subtask_id": "11",
        }
        assert client.hashes["ownership:test:subtask:11:stream"] == owned
        assert "ownership:test:subtask:12:stream" not in client.hashes

    asyncio.run(scenario())


@pytest.mark.parametrize("factory", _factories())
def test_same_subtask_restart_rejects_every_stale_generation_mutation(
    factory: StoreFactory,
) -> None:
    async def scenario() -> None:
        store = factory()
        old_generation = await store.start(task_id=7, subtask_id=12)
        new_generation = await store.start(task_id=7, subtask_id=12)
        assert old_generation != new_generation

        stale_operations = (
            lambda: store.append_text(
                subtask_id=12,
                generation_id=old_generation,
                block_id="old-text",
                offset=0,
                content="stale",
            ),
            lambda: store.upsert_block(
                subtask_id=12,
                generation_id=old_generation,
                block=create_text_block("stale", block_id="old-text"),
            ),
            lambda: store.set_status_snapshot(
                subtask_id=12,
                generation_id=old_generation,
                payload={"status": "stale"},
            ),
            lambda: store.set_cancelled(
                subtask_id=12,
                generation_id=old_generation,
            ),
            lambda: store.is_cancelled(
                subtask_id=12,
                generation_id=old_generation,
            ),
            lambda: store.validate_generation(
                task_id=7,
                subtask_id=12,
                generation_id=old_generation,
            ),
        )
        for operation in stale_operations:
            with pytest.raises(
                ChatStreamStaleGeneration,
                match="^chat_stream_stale_generation$",
            ):
                await operation()

        await store.finalize(
            task_id=7,
            subtask_id=12,
            generation_id=old_generation,
        )
        active = await store.get_active(task_id=7)
        assert active is not None
        assert active.generation_id == new_generation
        assert active.cached_content == ""
        assert active.blocks == ()
        assert active.status_updated is None
        assert (
            await store.is_cancelled(
                subtask_id=12,
                generation_id=new_generation,
            )
            is False
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("factory", _factories())
def test_offsets_use_javascript_utf16_code_units(factory: StoreFactory) -> None:
    async def scenario() -> None:
        store = factory()
        generation_id = await store.start(task_id=8, subtask_id=13)
        chunks = ("A", "😀", "e\u0301", "中", "𝄞")
        expected_offsets = (1, 3, 5, 6, 8)
        offset = 0
        for chunk, expected in zip(chunks, expected_offsets, strict=True):
            offset = await store.append_text(
                subtask_id=13,
                generation_id=generation_id,
                block_id="text",
                offset=offset,
                content=chunk,
            )
            assert offset == expected

        snapshot = await store.get_active(task_id=8)
        assert snapshot is not None
        assert snapshot.cached_content == "A😀e\u0301中𝄞"
        assert snapshot.offset == 8
        with pytest.raises(ChatStreamOffsetMismatch):
            await store.append_text(
                subtask_id=13,
                generation_id=generation_id,
                block_id="text",
                offset=7,
                content="bad",
            )

    asyncio.run(scenario())


def test_utf16_offset_helper_rejects_unpaired_surrogates() -> None:
    assert utf16_code_units("BMP中") == 4
    assert utf16_code_units("😀") == 2
    with pytest.raises(ValueError, match="^text_offset_invalid_text$"):
        utf16_code_units("\ud800")

    async def scenario() -> None:
        store = MemoryChatStreamStore()
        generation_id = await store.start(task_id=1, subtask_id=2)
        with pytest.raises(ValueError, match="^chat_stream_invalid_content$"):
            await store.append_text(
                subtask_id=2,
                generation_id=generation_id,
                block_id="text",
                offset=0,
                content="\ud800",
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("factory", _factories())
@pytest.mark.parametrize(
    "block_id",
    [
        "\u00a0",
        "\u2003",
        "é",
        "中",
        "!",
        "x" * (MAX_CHAT_BLOCK_ID_LENGTH + 1),
    ],
)
def test_store_rejects_block_ids_outside_bounded_ascii_grammar(
    factory: StoreFactory,
    block_id: str,
) -> None:
    async def scenario() -> None:
        store = factory()
        generation_id = await store.start(task_id=1, subtask_id=2)
        with pytest.raises(ValueError, match="^chat_stream_invalid_block_id$"):
            await store.append_text(
                subtask_id=2,
                generation_id=generation_id,
                block_id=block_id,
                offset=0,
                content="safe",
            )

        block = create_text_block("safe", block_id="valid")
        block["id"] = block_id
        with pytest.raises(ValueError, match="^chat_block_invalid_id$"):
            await store.upsert_block(
                subtask_id=2,
                generation_id=generation_id,
                block=block,
            )

        active = await store.get_active(task_id=1)
        assert active is not None
        assert active.cached_content == ""
        assert active.blocks == ()

    asyncio.run(scenario())


def test_memory_store_expires_on_access_with_injected_clock() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    current = [now]
    store = MemoryChatStreamStore(ttl_seconds=60, clock=lambda: current[0])

    async def scenario() -> None:
        generation_id = await store.start(task_id=1, subtask_id=2)
        current[0] += timedelta(seconds=59)
        await store.set_cancelled(subtask_id=2, generation_id=generation_id)
        current[0] += timedelta(seconds=59)
        assert await store.get_active(task_id=1) is not None
        current[0] += timedelta(seconds=2)
        assert await store.get_active(task_id=1) is None
        assert await store.is_cancelled(subtask_id=2, generation_id=generation_id) is False

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "store_type",
    [MemoryChatStreamStore, RedisChatStreamStore],
)
@pytest.mark.parametrize("ttl_seconds", [0, 1, 59, 86_401])
def test_store_constructors_share_public_ttl_bounds(store_type, ttl_seconds: int) -> None:
    kwargs = {"ttl_seconds": ttl_seconds}
    if store_type is RedisChatStreamStore:
        kwargs["client"] = FakeRedis()
    with pytest.raises(ValueError, match="^chat_stream_invalid_ttl$"):
        store_type(**kwargs)


def test_redis_mutations_refresh_task_and_subtask_ttls() -> None:
    client = FakeRedis()
    store = RedisChatStreamStore(client, ttl_seconds=60, key_prefix="ttl:test")

    async def scenario() -> None:
        generation_id = await store.start(task_id=1, subtask_id=2)
        await store.append_text(
            subtask_id=2,
            generation_id=generation_id,
            block_id="text",
            offset=0,
            content="x",
        )
        await store.set_cancelled(subtask_id=2, generation_id=generation_id)
        assert client.expire_calls["ttl:test:task:1:active"] == 3
        assert client.expire_calls["ttl:test:subtask:2:stream"] == 3

    asyncio.run(scenario())


def test_redis_malformed_state_has_stable_error() -> None:
    client = FakeRedis()
    store = RedisChatStreamStore(client, key_prefix="bad:test")

    async def scenario() -> None:
        await store.start(task_id=1, subtask_id=2)
        client.hashes["bad:test:subtask:2:stream"]["block_order"] = "not-json"
        with pytest.raises(ChatStreamMalformedState, match="^chat_stream_malformed_state$"):
            await store.get_active(task_id=1)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "corrupt_order",
    [
        "{}",
        "[1]",
        '["same","same"]',
        '["x",null,"y"]',
        '[""]',
        '[" "]',
        '["\u00a0"]',
        '["\u2003"]',
        '["é"]',
        '["中"]',
        '["!"]',
        '{"1":"x","3":"y"}',
        "not-json",
    ],
)
def test_redis_upsert_rejects_corrupt_block_order_stably(corrupt_order: str) -> None:
    client = FakeRedis()
    store = RedisChatStreamStore(client, key_prefix="order:test")

    async def scenario() -> None:
        generation_id = await store.start(task_id=1, subtask_id=2)
        state = client.hashes["order:test:subtask:2:stream"]
        state["block_order"] = corrupt_order
        before = deepcopy(state)

        with pytest.raises(
            ChatStreamMalformedState,
            match="^chat_stream_malformed_state$",
        ):
            await store.get_active(task_id=1)

        with pytest.raises(
            ChatStreamMalformedState,
            match="^chat_stream_malformed_state$",
        ):
            await store.upsert_block(
                subtask_id=2,
                generation_id=generation_id,
                block=create_text_block("safe", block_id="new"),
            )
        assert state == before

    asyncio.run(scenario())


def _real_redis_url() -> str:
    if os.environ.get("RUN_REDIS_INTEGRATION") != "1":
        pytest.skip("requires RUN_REDIS_INTEGRATION=1")
    redis_url = os.environ.get("REDIS_TEST_URL")
    if not redis_url:
        pytest.fail(
            "RUN_REDIS_INTEGRATION=1 requires an explicit REDIS_TEST_URL"
        )
    return redis_url


def test_real_redis_flag_requires_explicit_url(monkeypatch) -> None:
    monkeypatch.setenv("RUN_REDIS_INTEGRATION", "1")
    monkeypatch.delenv("REDIS_TEST_URL", raising=False)

    with pytest.raises(pytest.fail.Exception, match="explicit REDIS_TEST_URL"):
        _real_redis_url()


def test_real_redis_lua_rejects_corrupt_block_order_without_raw_errors() -> None:
    redis_url = _real_redis_url()
    prefix = f"test:chat-stream:{uuid4().hex}"
    client = AsyncRedis.from_url(redis_url, decode_responses=True)
    store = RedisChatStreamStore(client, key_prefix=prefix)

    async def scenario() -> None:
        try:
            assert await client.ping() is True
            old_generation = await store.start(task_id=101, subtask_id=202)
            generation_id = await store.start(task_id=101, subtask_id=202)
            with pytest.raises(ChatStreamStaleGeneration):
                await store.append_text(
                    subtask_id=202,
                    generation_id=old_generation,
                    block_id="old",
                    offset=0,
                    content="stale",
                )
            with pytest.raises(ChatStreamStaleGeneration):
                await store.set_cancelled(
                    subtask_id=202,
                    generation_id=old_generation,
                )
            with pytest.raises(ChatStreamStaleGeneration):
                await store.set_status_snapshot(
                    subtask_id=202,
                    generation_id=old_generation,
                    payload={"status": "stale"},
                )
            await store.finalize(
                task_id=101,
                subtask_id=202,
                generation_id=old_generation,
            )
            assert (
                await store.append_text(
                    subtask_id=202,
                    generation_id=generation_id,
                    block_id="text",
                    offset=0,
                    content="😀",
                )
                == 2
            )
            active = await store.get_active(task_id=101)
            assert active is not None
            assert active.generation_id == generation_id
            assert active.cached_content == "😀"
            assert active.offset == 2
            state_key = f"{prefix}:subtask:202:stream"
            for corrupt_order in (
                "{}",
                "[1]",
                '["same","same"]',
                '["x",null,"y"]',
                '[""]',
                '[" "]',
                '["\u00a0"]',
                '["\u2003"]',
                '["é"]',
                '["中"]',
                '["!"]',
                '{"1":"x","3":"y"}',
                "not-json",
            ):
                await client.hset(state_key, "block_order", corrupt_order)
                with pytest.raises(
                    ChatStreamMalformedState,
                    match="^chat_stream_malformed_state$",
                ):
                    await store.upsert_block(
                        subtask_id=202,
                        generation_id=generation_id,
                        block=create_text_block("safe", block_id="new"),
                    )
                assert await client.hget(state_key, "block_order") == corrupt_order
                assert await client.hexists(state_key, "block:new") is False

            valid_max_id = "x" * MAX_CHAT_BLOCK_ID_LENGTH
            await client.hset(
                state_key,
                "block_order",
                canonical_json(["Az09._:-", valid_max_id]),
            )
            await store.upsert_block(
                subtask_id=202,
                generation_id=generation_id,
                block=create_text_block("safe", block_id="new"),
            )
            assert json.loads(await client.hget(state_key, "block_order")) == [
                "Az09._:-",
                valid_max_id,
                "new",
            ]
        finally:
            await client.delete(
                f"{prefix}:task:101:active",
                f"{prefix}:subtask:202:stream",
            )
            await client.aclose()

    asyncio.run(scenario())


def test_real_redis_contains_corruption_and_keeps_snapshots_atomic() -> None:
    redis_url = _real_redis_url()
    prefix = f"test:chat-stream-hardening:{uuid4().hex}"
    client = AsyncRedis.from_url(redis_url, decode_responses=True)
    store = RedisChatStreamStore(client, key_prefix=prefix)
    task_key = f"{prefix}:task:301:active"
    other_task_key = f"{prefix}:task:302:active"
    first_key = f"{prefix}:subtask:401:stream"
    second_key = f"{prefix}:subtask:402:stream"
    third_key = f"{prefix}:subtask:403:stream"

    async def malformed(awaitable) -> None:
        with pytest.raises(
            ChatStreamMalformedState,
            match="^chat_stream_malformed_state$",
        ):
            await awaitable

    async def scenario() -> None:
        try:
            assert await client.ping() is True

            generation_id = await store.start(task_id=301, subtask_id=401)
            with pytest.raises(
                ChatStreamOffsetMismatch,
                match="^chat_stream_offset_mismatch$",
            ):
                await store.append_text(
                    subtask_id=401,
                    generation_id=generation_id,
                    block_id="text",
                    offset=1,
                    content="stale",
                )
            results = await asyncio.gather(
                store.append_text(
                    subtask_id=401,
                    generation_id=generation_id,
                    block_id="text",
                    offset=0,
                    content="a",
                ),
                store.append_text(
                    subtask_id=401,
                    generation_id=generation_id,
                    block_id="text",
                    offset=0,
                    content="b",
                ),
                return_exceptions=True,
            )
            assert sum(result == 1 for result in results) == 1
            assert sum(
                isinstance(result, ChatStreamOffsetMismatch) for result in results
            ) == 1
            await client.delete(task_key, first_key)

            await store.start(task_id=301, subtask_id=401)
            await store.start(task_id=302, subtask_id=402)
            await client.hset(task_key, "subtask_id", "402")
            owned = await client.hgetall(second_key)
            await malformed(store.start(task_id=301, subtask_id=403))
            assert await client.hget(task_key, "subtask_id") == "402"
            assert await client.hgetall(second_key) == owned
            assert await client.exists(third_key) == 0
            await client.delete(task_key, other_task_key, first_key, second_key)

            await client.set(task_key, "wrong-type")
            await malformed(store.get_active(task_id=301))
            await malformed(store.start(task_id=301, subtask_id=401))
            assert await client.get(task_key) == "wrong-type"
            await client.delete(task_key)

            generation_id = await store.start(task_id=301, subtask_id=401)
            await client.delete(first_key)
            await client.set(first_key, "wrong-type")
            await malformed(
                store.append_text(
                    subtask_id=401,
                    generation_id=generation_id,
                    block_id="text",
                    offset=0,
                    content="safe",
                ),
            )
            await malformed(
                store.upsert_block(
                    subtask_id=401,
                    generation_id=generation_id,
                    block=create_text_block("safe", block_id="text"),
                ),
            )
            await malformed(
                store.set_cancelled(
                    subtask_id=401,
                    generation_id=generation_id,
                ),
            )
            await malformed(
                store.is_cancelled(
                    subtask_id=401,
                    generation_id=generation_id,
                ),
            )
            await malformed(
                store.set_status_snapshot(
                    subtask_id=401,
                    generation_id=generation_id,
                    payload={"status": "safe"},
                ),
            )
            await malformed(store.get_active(task_id=301))
            await malformed(store.start(task_id=301, subtask_id=401))
            await malformed(
                store.finalize(
                    task_id=301,
                    subtask_id=401,
                    generation_id=generation_id,
                ),
            )
            assert await client.get(first_key) == "wrong-type"
            await client.delete(task_key, first_key)

            generation_id = await store.start(task_id=301, subtask_id=401)
            await client.hdel(first_key, "cached_content")
            before = await client.hgetall(first_key)
            await malformed(store.get_active(task_id=301))
            await malformed(
                store.append_text(
                    subtask_id=401,
                    generation_id=generation_id,
                    block_id="text",
                    offset=0,
                    content="safe",
                ),
            )
            await malformed(
                store.finalize(
                    task_id=301,
                    subtask_id=401,
                    generation_id=generation_id,
                ),
            )
            assert await client.hgetall(first_key) == before

            await client.hset(first_key, mapping={"cached_content": "😀", "offset": "1"})
            before = await client.hgetall(first_key)
            await malformed(store.get_active(task_id=301))
            await malformed(
                store.append_text(
                    subtask_id=401,
                    generation_id=generation_id,
                    block_id="text",
                    offset=1,
                    content="safe",
                ),
            )
            assert await client.hgetall(first_key) == before
            await client.delete(task_key, first_key)

            generation_id = await store.start(task_id=301, subtask_id=401)
            await client.hset(first_key, "cached_content", b"\xff")
            await malformed(store.get_active(task_id=301))
            await malformed(
                store.append_text(
                    subtask_id=401,
                    generation_id=generation_id,
                    block_id="text",
                    offset=0,
                    content="safe",
                ),
            )
            await client.delete(task_key, first_key)

            generation_id = await store.start(task_id=301, subtask_id=401)
            await store.set_status_snapshot(
                subtask_id=401,
                generation_id=generation_id,
                payload={"status": "safe"},
            )
            await client.hset(first_key, "status_updated", b"\xff")
            await malformed(store.get_active(task_id=301))
            await client.delete(task_key, first_key)

            await store.start(task_id=301, subtask_id=401)
            await client.hset(first_key, "block_order", b"\xff")
            await malformed(store.get_active(task_id=301))
            await client.delete(task_key, first_key)

            generation_id = await store.start(task_id=301, subtask_id=401)
            await store.upsert_block(
                subtask_id=401,
                generation_id=generation_id,
                block=create_text_block("safe", block_id="raw"),
            )
            await client.hset(first_key, "block:raw", b"\xff")
            await malformed(store.get_active(task_id=301))
            await client.delete(task_key, first_key)

            await store.start(task_id=301, subtask_id=401)
            assert await client.exists(first_key) == 1
            await store.start(task_id=301, subtask_id=402)
            assert await client.exists(first_key) == 0

            async def replace_active() -> None:
                for index in range(80):
                    await store.start(
                        task_id=301,
                        subtask_id=401 + (index % 2),
                    )

            async def read_active() -> None:
                for _ in range(160):
                    active = await store.get_active(task_id=301)
                    assert active is not None
                    assert active.task_id == 301
                    assert active.subtask_id in {401, 402}

            await asyncio.gather(replace_active(), read_active(), read_active())
            active = await store.get_active(task_id=301)
            assert active is not None
            assert await client.exists(
                first_key if active.subtask_id == 402 else second_key,
            ) == 0

            ttl_prefix = f"{prefix}:ttl"
            ttl_store = RedisChatStreamStore(
                client,
                ttl_seconds=60,
                key_prefix=ttl_prefix,
            )
            ttl_generation = await ttl_store.start(task_id=501, subtask_id=601)
            ttl_task_key = f"{ttl_prefix}:task:501:active"
            ttl_stream_key = f"{ttl_prefix}:subtask:601:stream"
            await client.expire(ttl_task_key, 2)
            await client.expire(ttl_stream_key, 2)
            await asyncio.sleep(1.1)
            await ttl_store.set_cancelled(
                subtask_id=601,
                generation_id=ttl_generation,
            )
            assert await client.ttl(ttl_task_key) >= 59
            assert await client.ttl(ttl_stream_key) >= 59
            await client.expire(ttl_task_key, 2)
            await client.expire(ttl_stream_key, 2)
            await asyncio.sleep(1.1)
            assert await ttl_store.get_active(task_id=501) is not None
            await asyncio.sleep(1.1)
            assert await ttl_store.get_active(task_id=501) is None
        finally:
            await client.delete(
                task_key,
                other_task_key,
                first_key,
                second_key,
                third_key,
                f"{prefix}:ttl:task:501:active",
                f"{prefix}:ttl:subtask:601:stream",
            )
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [0, -1, True])
def test_store_rejects_non_positive_ids(value: int) -> None:
    async def scenario() -> None:
        store = MemoryChatStreamStore()
        with pytest.raises(ValueError, match="chat_stream_invalid_task_id"):
            await store.start(task_id=value, subtask_id=1)

    asyncio.run(scenario())


def test_build_chat_realtime_reuses_successful_probe_client(monkeypatch) -> None:
    client = FakeRedis()
    monkeypatch.setattr(
        "app.services.chat_stream_builder.Redis.from_url",
        lambda *_args, **_kwargs: client,
    )

    async def scenario() -> None:
        backend = await build_chat_realtime(Settings(_env_file=None))
        assert backend.redis_available is True
        assert backend.backend == "redis"
        assert backend.degraded is False
        assert backend.redis_client is client
        assert isinstance(backend.stream_store, RedisChatStreamStore)
        assert client.close_calls == 0
        await backend.aclose()
        assert client.closed is True
        assert client.close_calls == 1

    asyncio.run(scenario())


def test_build_chat_realtime_closes_failed_probe_and_degrades(monkeypatch) -> None:
    client = FakeRedis(ping_error=ConnectionError("redis unavailable with secret"))
    monkeypatch.setattr(
        "app.services.chat_stream_builder.Redis.from_url",
        lambda *_args, **_kwargs: client,
    )

    async def scenario() -> None:
        backend = await build_chat_realtime(Settings(_env_file=None))
        assert backend.redis_available is False
        assert backend.backend == "memory"
        assert backend.degraded is True
        assert backend.redis_client is None
        assert isinstance(backend.stream_store, MemoryChatStreamStore)
        assert client.closed is True
        assert client.close_calls == 1

    asyncio.run(scenario())


def test_build_chat_realtime_cancellation_closes_probe_client_once(monkeypatch) -> None:
    client = BlockingPingRedis()
    monkeypatch.setattr(
        "app.services.chat_stream_builder.Redis.from_url",
        lambda *_args, **_kwargs: client,
    )

    async def scenario() -> None:
        build_task = asyncio.create_task(
            build_chat_realtime(Settings(_env_file=None)),
        )
        await client.ping_started.wait()
        build_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await build_task
        assert client.closed is True
        assert client.close_calls == 1

    asyncio.run(scenario())


def test_build_chat_realtime_degrades_when_client_construction_fails(
    monkeypatch,
    caplog,
) -> None:
    secret = "redis://:do-not-log@invalid:6379/0"

    def fail_from_url(*_args, **_kwargs):
        raise ValueError(f"bad url {secret}")

    monkeypatch.setattr(
        "app.services.chat_stream_builder.Redis.from_url",
        fail_from_url,
    )

    backend = asyncio.run(
        build_chat_realtime(Settings(_env_file=None).model_copy(update={"redis_url": secret}))
    )

    assert backend.backend == "memory"
    assert backend.degraded is True
    assert secret not in caplog.text


def test_build_chat_realtime_closes_client_when_store_construction_fails(
    monkeypatch,
) -> None:
    client = FakeRedis()
    monkeypatch.setattr(
        "app.services.chat_stream_builder.Redis.from_url",
        lambda *_args, **_kwargs: client,
    )

    class BrokenRedisStore:
        def __init__(self, *_args, **_kwargs) -> None:
            raise ValueError("invalid prefix containing a secret")

    monkeypatch.setattr(
        "app.services.chat_stream_builder.RedisChatStreamStore",
        BrokenRedisStore,
    )

    backend = asyncio.run(build_chat_realtime(Settings(_env_file=None)))

    assert backend.backend == "memory"
    assert backend.degraded is True
    assert client.close_calls == 1


def test_build_chat_realtime_degrades_for_invalid_prefix_and_closes_client(
    monkeypatch,
) -> None:
    client = FakeRedis()
    monkeypatch.setattr(
        "app.services.chat_stream_builder.Redis.from_url",
        lambda *_args, **_kwargs: client,
    )
    settings = Settings(_env_file=None).model_copy(update={"chat_stream_key_prefix": ":"})

    backend = asyncio.run(build_chat_realtime(settings))

    assert backend.backend == "memory"
    assert backend.degraded is True
    assert client.close_calls == 1
