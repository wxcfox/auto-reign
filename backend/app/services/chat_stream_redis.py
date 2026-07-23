from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from typing import cast
from uuid import uuid4

from redis.asyncio import Redis

from app.core.limits import MAX_CHAT_BLOCK_ID_LENGTH
from app.services.chat_blocks import copy_chat_block, is_valid_chat_block_id
from app.services.chat_stream_types import (
    ActiveStreamSnapshot,
    ChatStreamMalformedState,
    _block_id,
    _check_result,
    _decode_text,
    _generation_id,
    _json_object,
    _offset,
    _parse_positive,
    _positive_id,
    _result_code,
    _text,
    _timestamp,
    _utc_now,
    _validate_timestamp,
)
from app.services.json_safety import (
    MAX_JSON_CANONICAL_BYTES,
    JsonSafetyError,
    canonical_json,
)
from app.services.text_offsets import advance_utf16_offset, utf16_code_units


_START_SCRIPT = """-- chat_stream:start
local function complete_stream(key)
  local values = redis.call('HMGET', key, 'task_id', 'subtask_id', 'generation_id',
    'offset', 'cached_content', 'block_order', 'started_at', 'last_activity_at', 'cancelled')
  for _, value in ipairs(values) do if not value then return false end end
  return true
end
local task_type = redis.call('TYPE', KEYS[1]).ok
local subtask_type = redis.call('TYPE', KEYS[2]).ok
if (task_type ~= 'none' and task_type ~= 'hash')
  or (subtask_type ~= 'none' and subtask_type ~= 'hash') then
  return {'malformed'}
end
local old_subtask = redis.call('HGET', KEYS[1], 'subtask_id')
if task_type == 'hash' and (not old_subtask or not string.match(old_subtask, '^%d+$')) then
  return {'malformed'}
end
if old_subtask and old_subtask ~= ARGV[2] then
  local old_stream_key = ARGV[1] .. ':subtask:' .. old_subtask .. ':stream'
  if redis.call('TYPE', old_stream_key).ok ~= 'hash'
    or not complete_stream(old_stream_key) then return {'malformed'} end
  local owner = redis.call('HMGET', old_stream_key, 'task_id', 'subtask_id')
  if owner[1] ~= ARGV[4] or owner[2] ~= old_subtask then return {'malformed'} end
end
local old_task = redis.call('HGET', KEYS[2], 'task_id')
if subtask_type == 'hash' then
  if not complete_stream(KEYS[2])
    or not old_task or not string.match(old_task, '^%d+$') then return {'malformed'} end
end
if old_task and old_task ~= ARGV[4] then
  local old_task_key = ARGV[1] .. ':task:' .. old_task .. ':active'
  if redis.call('TYPE', old_task_key).ok ~= 'hash' then return {'malformed'} end
  local old_task_pointer = redis.call('HGET', old_task_key, 'subtask_id')
  if not old_task_pointer or not string.match(old_task_pointer, '^%d+$') then
    return {'malformed'}
  end
end
if old_subtask and old_subtask ~= ARGV[2] then
  redis.call('DEL', ARGV[1] .. ':subtask:' .. old_subtask .. ':stream')
end
if old_task and old_task ~= ARGV[4] then
  local old_task_key = ARGV[1] .. ':task:' .. old_task .. ':active'
  if redis.call('HGET', old_task_key, 'subtask_id') == ARGV[2] then
    redis.call('DEL', old_task_key)
  end
end
redis.call('HSET', KEYS[1], 'subtask_id', ARGV[2])
redis.call('EXPIRE', KEYS[1], ARGV[3])
redis.call('DEL', KEYS[2])
redis.call('HSET', KEYS[2],
  'task_id', ARGV[4], 'subtask_id', ARGV[2], 'offset', '0',
  'cached_content', '', 'block_order', '[]', 'started_at', ARGV[5],
  'last_activity_at', ARGV[5], 'cancelled', '0', 'generation_id', ARGV[6])
redis.call('EXPIRE', KEYS[2], ARGV[3])
return {'ok'}
"""

_APPEND_SCRIPT = """-- chat_stream:append
local function utf16_units(value, max_bytes)
  if #value > max_bytes then return nil end
  local index = 1
  local units = 0
  while index <= #value do
    local first = string.byte(value, index)
    if first <= 0x7f then index = index + 1; units = units + 1
    elseif first >= 0xc2 and first <= 0xdf then
      local second = string.byte(value, index + 1)
      if not second or second < 0x80 or second > 0xbf then return nil end
      index = index + 2; units = units + 1
    elseif first >= 0xe0 and first <= 0xef then
      local second = string.byte(value, index + 1)
      local third = string.byte(value, index + 2)
      if not second or not third or third < 0x80 or third > 0xbf then return nil end
      if (first == 0xe0 and (second < 0xa0 or second > 0xbf))
        or (first == 0xed and (second < 0x80 or second > 0x9f))
        or (first ~= 0xe0 and first ~= 0xed and (second < 0x80 or second > 0xbf)) then
        return nil
      end
      index = index + 3; units = units + 1
    elseif first >= 0xf0 and first <= 0xf4 then
      local second = string.byte(value, index + 1)
      local third = string.byte(value, index + 2)
      local fourth = string.byte(value, index + 3)
      if not second or not third or not fourth
        or third < 0x80 or third > 0xbf or fourth < 0x80 or fourth > 0xbf then return nil end
      if (first == 0xf0 and (second < 0x90 or second > 0xbf))
        or (first == 0xf4 and (second < 0x80 or second > 0x8f))
        or (first ~= 0xf0 and first ~= 0xf4 and (second < 0x80 or second > 0xbf)) then
        return nil
      end
      index = index + 4; units = units + 2
    else return nil end
  end
  return units
end
local stream_type = redis.call('TYPE', KEYS[1]).ok
if stream_type == 'none' then return {'not_active'} end
if stream_type ~= 'hash' then return {'malformed'} end
local fields = redis.call('HMGET', KEYS[1], 'offset', 'task_id', 'subtask_id',
  'generation_id', 'cached_content', 'block_order', 'started_at',
  'last_activity_at', 'cancelled')
for _, value in ipairs(fields) do if not value then return {'malformed'} end end
local current, task_id, subtask_id, generation_id = fields[1], fields[2], fields[3], fields[4]
local cached_content = fields[5]
if not string.match(current, '^%d+$') or not string.match(task_id, '^%d+$')
  or not string.match(subtask_id, '^%d+$') or (fields[9] ~= '0' and fields[9] ~= '1') then
  return {'malformed'}
end
if generation_id ~= ARGV[7] then return {'stale'} end
local cached_units = utf16_units(cached_content, tonumber(ARGV[9]))
local chunk_units = utf16_units(ARGV[2], tonumber(ARGV[9]))
if not cached_units or not chunk_units or cached_units ~= tonumber(current)
  or chunk_units ~= tonumber(ARGV[8]) then return {'malformed'} end
if current ~= ARGV[1] then return {'offset'} end
if tonumber(ARGV[3]) ~= tonumber(current) + chunk_units then return {'malformed'} end
local task_key = ARGV[6] .. ':task:' .. task_id .. ':active'
if redis.call('TYPE', task_key).ok ~= 'hash' then return {'malformed'} end
if redis.call('HGET', task_key, 'subtask_id') ~= subtask_id then
  return {'not_active'}
end
redis.call('HSET', KEYS[1], 'cached_content',
  cached_content .. ARGV[2],
  'offset', ARGV[3], 'last_activity_at', ARGV[4])
redis.call('EXPIRE', KEYS[1], ARGV[5])
redis.call('EXPIRE', task_key, ARGV[5])
return {'ok', ARGV[3]}
"""

_UPSERT_SCRIPT = """-- chat_stream:upsert
local stream_type = redis.call('TYPE', KEYS[1]).ok
if stream_type == 'none' then return {'not_active'} end
if stream_type ~= 'hash' then return {'malformed'} end
local task_id = redis.call('HGET', KEYS[1], 'task_id')
local subtask_id = redis.call('HGET', KEYS[1], 'subtask_id')
local order_json = redis.call('HGET', KEYS[1], 'block_order')
local generation_id = redis.call('HGET', KEYS[1], 'generation_id')
local required = redis.call('HMGET', KEYS[1], 'offset', 'cached_content',
  'started_at', 'last_activity_at', 'cancelled')
for _, value in ipairs(required) do if not value then return {'malformed'} end end
if not task_id or not subtask_id or not order_json or not generation_id
  or not string.match(task_id, '^%d+$') or not string.match(subtask_id, '^%d+$')
  or not string.match(required[1], '^%d+$')
  or (required[5] ~= '0' and required[5] ~= '1') then
  return {'malformed'}
end
if generation_id ~= ARGV[7] then return {'stale'} end
local task_key = ARGV[6] .. ':task:' .. task_id .. ':active'
if redis.call('TYPE', task_key).ok ~= 'hash' then return {'malformed'} end
if redis.call('HGET', task_key, 'subtask_id') ~= subtask_id then
  return {'not_active'}
end
if not string.match(order_json, '^%s*%[') or not string.match(order_json, '%]%s*$') then
  return {'malformed'}
end
local ok, order = pcall(cjson.decode, order_json)
if not ok or type(order) ~= 'table' then return {'malformed'} end
local count = 0
local ids = {}
for key, value in pairs(order) do
  if type(key) ~= 'number' or key < 1 or key % 1 ~= 0 then return {'malformed'} end
  if type(value) ~= 'string'
    or #value > tonumber(ARGV[8])
    or not string.match(value, '^[%w%._:%-]+$')
    or ids[value] then
    return {'malformed'}
  end
  count = count + 1
  ids[value] = true
end
for index = 1, count do
  if order[index] == nil then return {'malformed'} end
end
if not ids[ARGV[1]] then table.insert(order, ARGV[1]) end
redis.call('HSET', KEYS[1], 'block_order', cjson.encode(order),
  'block:' .. ARGV[1], ARGV[2], 'last_activity_at', ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[4])
redis.call('EXPIRE', task_key, ARGV[4])
return {'ok'}
"""

_MUTATE_SCRIPT = """-- chat_stream:mutate
local stream_type = redis.call('TYPE', KEYS[1]).ok
if stream_type == 'none' then return {'not_active'} end
if stream_type ~= 'hash' then return {'malformed'} end
local task_id = redis.call('HGET', KEYS[1], 'task_id')
local subtask_id = redis.call('HGET', KEYS[1], 'subtask_id')
local generation_id = redis.call('HGET', KEYS[1], 'generation_id')
local required = redis.call('HMGET', KEYS[1], 'offset', 'cached_content',
  'block_order', 'started_at', 'last_activity_at', 'cancelled')
for _, value in ipairs(required) do if not value then return {'malformed'} end end
if not task_id or not subtask_id or not generation_id
  or not string.match(task_id, '^%d+$') or not string.match(subtask_id, '^%d+$')
  or not string.match(required[1], '^%d+$')
  or (required[6] ~= '0' and required[6] ~= '1') then return {'malformed'} end
if generation_id ~= ARGV[7] then return {'stale'} end
local task_key = ARGV[6] .. ':task:' .. task_id .. ':active'
if redis.call('TYPE', task_key).ok ~= 'hash' then return {'malformed'} end
if redis.call('HGET', task_key, 'subtask_id') ~= subtask_id then
  return {'not_active'}
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2], 'last_activity_at', ARGV[3])
redis.call('EXPIRE', KEYS[1], ARGV[4])
redis.call('EXPIRE', task_key, ARGV[4])
return {'ok'}
"""

_FINALIZE_SCRIPT = """-- chat_stream:finalize
local task_type = redis.call('TYPE', KEYS[1]).ok
local stream_type = redis.call('TYPE', KEYS[2]).ok
if task_type == 'none' or stream_type == 'none' then return {'stale'} end
if task_type ~= 'hash' or stream_type ~= 'hash' then return {'malformed'} end
local pointer = redis.call('HGET', KEYS[1], 'subtask_id')
local values = redis.call('HMGET', KEYS[2], 'task_id', 'subtask_id', 'generation_id',
  'offset', 'cached_content', 'block_order', 'started_at', 'last_activity_at', 'cancelled')
if not pointer then return {'malformed'} end
for _, value in ipairs(values) do if not value then return {'malformed'} end end
if pointer ~= ARGV[1] or values[1] ~= ARGV[2] or values[2] ~= ARGV[1]
  or values[3] ~= ARGV[3] then return {'stale'} end
redis.call('DEL', KEYS[1], KEYS[2])
return {'ok'}
"""

_VALIDATE_SCRIPT = """-- chat_stream:validate
local task_type = redis.call('TYPE', KEYS[1]).ok
local stream_type = redis.call('TYPE', KEYS[2]).ok
if task_type == 'none' or stream_type == 'none' then return {'not_active'} end
if task_type ~= 'hash' or stream_type ~= 'hash' then return {'malformed'} end
local pointer = redis.call('HGET', KEYS[1], 'subtask_id')
local values = redis.call('HMGET', KEYS[2], 'task_id', 'subtask_id', 'generation_id',
  'offset', 'cached_content', 'block_order', 'started_at', 'last_activity_at', 'cancelled')
if not pointer then return {'malformed'} end
for _, value in ipairs(values) do if not value then return {'malformed'} end end
if not string.match(pointer, '^%d+$') or not string.match(values[1], '^%d+$')
  or not string.match(values[2], '^%d+$') or not string.match(values[4], '^%d+$')
  or (values[9] ~= '0' and values[9] ~= '1') then return {'malformed'} end
if pointer ~= ARGV[1] or values[1] ~= ARGV[2] or values[2] ~= ARGV[1]
  or values[3] ~= ARGV[3] then return {'stale'} end
return {'ok'}
"""

_SNAPSHOT_SCRIPT = """-- chat_stream:snapshot
local function utf16_units(value, max_bytes)
  if #value > max_bytes then return nil end
  local index = 1
  local units = 0
  while index <= #value do
    local first = string.byte(value, index)
    if first <= 0x7f then index = index + 1; units = units + 1
    elseif first >= 0xc2 and first <= 0xdf then
      local second = string.byte(value, index + 1)
      if not second or second < 0x80 or second > 0xbf then return nil end
      index = index + 2; units = units + 1
    elseif first >= 0xe0 and first <= 0xef then
      local second = string.byte(value, index + 1)
      local third = string.byte(value, index + 2)
      if not second or not third or third < 0x80 or third > 0xbf then return nil end
      if (first == 0xe0 and (second < 0xa0 or second > 0xbf))
        or (first == 0xed and (second < 0x80 or second > 0x9f))
        or (first ~= 0xe0 and first ~= 0xed and (second < 0x80 or second > 0xbf)) then
        return nil
      end
      index = index + 3; units = units + 1
    elseif first >= 0xf0 and first <= 0xf4 then
      local second = string.byte(value, index + 1)
      local third = string.byte(value, index + 2)
      local fourth = string.byte(value, index + 3)
      if not second or not third or not fourth
        or third < 0x80 or third > 0xbf or fourth < 0x80 or fourth > 0xbf then return nil end
      if (first == 0xf0 and (second < 0x90 or second > 0xbf))
        or (first == 0xf4 and (second < 0x80 or second > 0x8f))
        or (first ~= 0xf0 and first ~= 0xf4 and (second < 0x80 or second > 0xbf)) then
        return nil
      end
      index = index + 4; units = units + 2
    else return nil end
  end
  return units
end
local task_type = redis.call('TYPE', KEYS[1]).ok
if task_type == 'none' then return {'none'} end
if task_type ~= 'hash' then return {'malformed'} end
local subtask_id = redis.call('HGET', KEYS[1], 'subtask_id')
if not subtask_id or not string.match(subtask_id, '^%d+$') then return {'malformed'} end
local stream_key = ARGV[1] .. ':subtask:' .. subtask_id .. ':stream'
if redis.call('TYPE', stream_key).ok ~= 'hash' then return {'malformed'} end
local values = redis.call('HMGET', stream_key, 'task_id', 'subtask_id', 'generation_id',
  'offset', 'cached_content', 'block_order', 'started_at', 'last_activity_at',
  'cancelled', 'status_updated')
for index = 1, 9 do if not values[index] then return {'malformed'} end end
if values[1] ~= ARGV[2] or values[2] ~= subtask_id or values[3] == ''
  or not string.match(values[4], '^%d+$')
  or (values[9] ~= '0' and values[9] ~= '1') then return {'malformed'} end
local units = utf16_units(values[5], tonumber(ARGV[4]))
if not units or units ~= tonumber(values[4]) then return {'malformed'} end
if not utf16_units(values[6], tonumber(ARGV[4]))
  or (values[10] and not utf16_units(values[10], tonumber(ARGV[4]))) then
  return {'malformed'}
end
local ok, order = pcall(cjson.decode, values[6])
if not ok or type(order) ~= 'table' or not string.match(values[6], '^%s*%[') then
  return {'malformed'}
end
local blocks = cjson.decode('[]')
local seen = {}
local count = 0
for key, block_id in pairs(order) do
  if type(key) ~= 'number' or key < 1 or key % 1 ~= 0
    or type(block_id) ~= 'string' or #block_id > tonumber(ARGV[3])
    or not string.match(block_id, '^[%w%._:%-]+$') or seen[block_id] then
    return {'malformed'}
  end
  count = count + 1; seen[block_id] = true
end
for index = 1, count do
  if not order[index] then return {'malformed'} end
  local block = redis.call('HGET', stream_key, 'block:' .. order[index])
  if not block or not utf16_units(block, tonumber(ARGV[4])) then return {'malformed'} end
  blocks[index] = block
end
local response = {'ok', values[1], values[2], values[3], values[4], values[5],
  values[6], values[7], values[8], values[9], values[10] or ''}
for index = 1, count do table.insert(response, blocks[index]) end
return response
"""

_CANCELLED_SCRIPT = """-- chat_stream:cancelled
local stream_type = redis.call('TYPE', KEYS[1]).ok
if stream_type == 'none' then return {'none'} end
if stream_type ~= 'hash' then return {'malformed'} end
local values = redis.call('HMGET', KEYS[1], 'task_id', 'subtask_id', 'generation_id',
  'offset', 'cached_content', 'block_order', 'started_at', 'last_activity_at', 'cancelled')
for _, value in ipairs(values) do if not value then return {'malformed'} end end
if values[3] ~= ARGV[1] then return {'stale'} end
if not string.match(values[1], '^%d+$') or not string.match(values[2], '^%d+$')
  or not string.match(values[4], '^%d+$') or (values[9] ~= '0' and values[9] ~= '1') then
  return {'malformed'}
end
local task_key = ARGV[2] .. ':task:' .. values[1] .. ':active'
if redis.call('TYPE', task_key).ok ~= 'hash'
  or redis.call('HGET', task_key, 'subtask_id') ~= values[2] then return {'malformed'} end
return {'ok', values[9]}
"""


class RedisChatStreamStore:
    """Ephemeral stream state for one standalone Redis deployment.

    Redis Cluster is intentionally unsupported because the atomic scripts touch
    task and subtask keys without cluster hash tags.
    """
    def __init__(
        self,
        client: Redis,
        *,
        ttl_seconds: int = 3600,
        key_prefix: str = "auto_reign:chat",
        owns_client: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 60 <= ttl_seconds <= 86_400:
            raise ValueError("chat_stream_invalid_ttl")
        prefix = key_prefix.strip().strip(":")
        if not prefix:
            raise ValueError("chat_stream_invalid_key_prefix")
        self.client = client
        self.ttl_seconds = ttl_seconds
        self.key_prefix = prefix
        self.owns_client = owns_client
        self._clock = clock or (lambda: datetime.now(UTC))

    async def start(self, *, task_id: int, subtask_id: int) -> str:
        _positive_id(task_id, "task_id")
        _positive_id(subtask_id, "subtask_id")
        generation_id = str(uuid4())
        now = _timestamp(_utc_now(self._clock))
        result = await self.client.eval(
            _START_SCRIPT,
            2,
            self._task_key(task_id),
            self._subtask_key(subtask_id),
            self.key_prefix,
            str(subtask_id),
            str(self.ttl_seconds),
            str(task_id),
            now,
            generation_id,
        )
        _check_result(result)
        return generation_id

    async def get_active(self, *, task_id: int) -> ActiveStreamSnapshot | None:
        _positive_id(task_id, "task_id")
        result = await self.client.eval(
            _SNAPSHOT_SCRIPT,
            1,
            self._task_key(task_id),
            self.key_prefix,
            str(task_id),
            str(MAX_CHAT_BLOCK_ID_LENGTH),
            str(MAX_JSON_CANONICAL_BYTES),
        )
        code = _result_code(result)
        if code == "none":
            return None
        if code != "ok" or len(result) < 11:
            raise ChatStreamMalformedState()
        try:
            values = [_decode_text(value) for value in result[1:]]
            subtask_id = _parse_positive(values[1])
            order = json.loads(values[5])
            blocks = values[10:]
            if (
                subtask_id is None
                or not isinstance(order, list)
                or len(blocks) != len(order)
            ):
                raise ChatStreamMalformedState()
            state = {
                "task_id": values[0],
                "subtask_id": values[1],
                "generation_id": values[2],
                "offset": values[3],
                "cached_content": values[4],
                "block_order": values[5],
                "started_at": values[6],
                "last_activity_at": values[7],
                "cancelled": values[8],
            }
            if values[9]:
                state["status_updated"] = values[9]
            for block_id, block in zip(order, blocks, strict=True):
                state[f"block:{block_id}"] = block
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, OverflowError):
            raise ChatStreamMalformedState() from None
        return _snapshot_from_redis(task_id, subtask_id, state)

    async def validate_generation(
        self,
        *,
        task_id: int,
        subtask_id: int,
        generation_id: str,
    ) -> None:
        _positive_id(task_id, "task_id")
        _positive_id(subtask_id, "subtask_id")
        _generation_id(generation_id)
        result = await self.client.eval(
            _VALIDATE_SCRIPT,
            2,
            self._task_key(task_id),
            self._subtask_key(subtask_id),
            str(subtask_id),
            str(task_id),
            generation_id,
        )
        _check_result(result)

    async def append_text(
        self,
        *,
        subtask_id: int,
        generation_id: str,
        block_id: str,
        offset: int,
        content: str,
    ) -> int:
        _positive_id(subtask_id, "subtask_id")
        _generation_id(generation_id)
        _block_id(block_id)
        _offset(offset)
        _text(content)
        new_offset = advance_utf16_offset(offset, content)
        result = await self.client.eval(
            _APPEND_SCRIPT,
            1,
            self._subtask_key(subtask_id),
            str(offset),
            content,
            str(new_offset),
            _timestamp(_utc_now(self._clock)),
            str(self.ttl_seconds),
            self.key_prefix,
            generation_id,
            str(utf16_code_units(content)),
            str(MAX_JSON_CANONICAL_BYTES),
        )
        _check_result(result)
        return new_offset

    async def upsert_block(
        self,
        *,
        subtask_id: int,
        generation_id: str,
        block: dict[str, object],
    ) -> None:
        _positive_id(subtask_id, "subtask_id")
        _generation_id(generation_id)
        canonical = dict(copy_chat_block(block))
        block_id = cast(str, canonical["id"])
        result = await self.client.eval(
            _UPSERT_SCRIPT,
            1,
            self._subtask_key(subtask_id),
            block_id,
            canonical_json(canonical),
            _timestamp(_utc_now(self._clock)),
            str(self.ttl_seconds),
            "unused",
            self.key_prefix,
            generation_id,
            str(MAX_CHAT_BLOCK_ID_LENGTH),
        )
        _check_result(result)

    async def set_cancelled(self, *, subtask_id: int, generation_id: str) -> None:
        await self._mutate(subtask_id, generation_id, "cancelled", "1")

    async def is_cancelled(self, *, subtask_id: int, generation_id: str) -> bool:
        _positive_id(subtask_id, "subtask_id")
        _generation_id(generation_id)
        result = await self.client.eval(
            _CANCELLED_SCRIPT,
            1,
            self._subtask_key(subtask_id),
            generation_id,
            self.key_prefix,
        )
        code = _result_code(result)
        if code == "none":
            return False
        _check_result(result)
        if len(result) != 2:
            raise ChatStreamMalformedState()
        return _decode_text(result[1]) == "1"

    async def set_status_snapshot(
        self,
        *,
        subtask_id: int,
        generation_id: str,
        payload: dict[str, object],
    ) -> None:
        await self._mutate(
            subtask_id,
            generation_id,
            "status_updated",
            canonical_json(_json_object(payload)),
        )

    async def finalize(
        self,
        *,
        task_id: int,
        subtask_id: int,
        generation_id: str,
    ) -> None:
        _positive_id(task_id, "task_id")
        _positive_id(subtask_id, "subtask_id")
        _generation_id(generation_id)
        result = await self.client.eval(
            _FINALIZE_SCRIPT,
            2,
            self._task_key(task_id),
            self._subtask_key(subtask_id),
            str(subtask_id),
            str(task_id),
            generation_id,
        )
        if _result_code(result) not in {"ok", "stale"}:
            _check_result(result)

    async def aclose(self) -> None:
        if self.owns_client:
            await self.client.aclose()

    async def _mutate(
        self,
        subtask_id: int,
        generation_id: str,
        field: str,
        value: str,
    ) -> None:
        _positive_id(subtask_id, "subtask_id")
        _generation_id(generation_id)
        result = await self.client.eval(
            _MUTATE_SCRIPT,
            1,
            self._subtask_key(subtask_id),
            field,
            value,
            _timestamp(_utc_now(self._clock)),
            str(self.ttl_seconds),
            "unused",
            self.key_prefix,
            generation_id,
        )
        _check_result(result)

    def _task_key(self, task_id: int) -> str:
        return f"{self.key_prefix}:task:{task_id}:active"

    def _subtask_key(self, subtask_id: int) -> str:
        return f"{self.key_prefix}:subtask:{subtask_id}:stream"


def _snapshot_from_redis(
    expected_task_id: int,
    expected_subtask_id: int,
    state: dict[str, str],
) -> ActiveStreamSnapshot:
    task_id = _parse_positive(state.get("task_id"))
    subtask_id = _parse_positive(state.get("subtask_id"))
    generation_id = state.get("generation_id")
    try:
        offset = int(state["offset"])
        content = state["cached_content"]
        content_offset = utf16_code_units(content)
        started_at = state["started_at"]
        last_activity_at = state["last_activity_at"]
        order = json.loads(state["block_order"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OverflowError):
        raise ChatStreamMalformedState() from None
    if (
        task_id != expected_task_id
        or subtask_id != expected_subtask_id
        or not isinstance(generation_id, str)
        or not generation_id.strip()
        or offset < 0
        or offset != content_offset
        or not isinstance(order, list)
        or any(not is_valid_chat_block_id(item) for item in order)
        or len(set(order)) != len(order)
    ):
        raise ChatStreamMalformedState()
    _validate_timestamp(started_at)
    _validate_timestamp(last_activity_at)
    blocks: list[dict[str, object]] = []
    try:
        for block_id in order:
            raw_block = state[f"block:{block_id}"]
            decoded = json.loads(raw_block)
            canonical = dict(copy_chat_block(decoded))
            if canonical["id"] != block_id:
                raise ChatStreamMalformedState()
            blocks.append(canonical)
        raw_status = state.get("status_updated")
        status = None if raw_status is None else _json_object(json.loads(raw_status))
    except (
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        JsonSafetyError,
        RecursionError,
        OverflowError,
    ):
        raise ChatStreamMalformedState() from None
    return ActiveStreamSnapshot(
        task_id=task_id,
        subtask_id=subtask_id,
        generation_id=generation_id,
        offset=offset,
        cached_content=content,
        blocks=tuple(blocks),
        started_at=started_at,
        last_activity_at=last_activity_at,
        status_updated=status,
    )
