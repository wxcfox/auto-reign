import { describe, expect, it } from "vitest";

import type { ActiveStreamSnapshot } from "@/lib/socket-types";
import type { ChatBlock, Subtask } from "@/lib/types";

import {
  initialTaskChatState,
  maxDurableMessageId,
  reconnectCursorMessageId,
  reduceTaskChat,
  selectBlocks,
} from "./task-chat-reducer";

const timestamp = "2026-07-22T00:00:00Z";

function textBlock(id: string, content = ""): ChatBlock {
  return { id, type: "text", content, status: "streaming", timestamp };
}

function subtask(overrides: Partial<Subtask> & Pick<Subtask, "id" | "role">): Subtask {
  const { id, role, ...rest } = overrides;
  return {
    id,
    task_id: 7,
    role,
    message_id: id,
    parent_id: null,
    prompt: "",
    status: "COMPLETED",
    progress: 100,
    result: null,
    error_message: null,
    contexts: [],
    created_at: timestamp,
    updated_at: timestamp,
    completed_at: timestamp,
    ...rest,
  };
}

describe("reduceTaskChat", () => {
  it("merges tool updates, preserves order, and ignores duplicate text offsets", () => {
    let state = initialTaskChatState(7);
    const initial = state;
    state = reduceTaskChat(state, {
      type: "start",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-1",
    });
    state = reduceTaskChat(state, {
      type: "block-created",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-1",
      block: {
        id: "tool-1",
        type: "tool",
        tool_use_id: "call-1",
        tool_name: "search",
        tool_input: {},
        status: "pending",
        timestamp,
      },
    });
    const beforeUpdate = state;
    state = reduceTaskChat(state, {
      type: "block-updated",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-1",
      blockId: "tool-1",
      patch: { tool_output: "found", status: "done" },
    });
    state = reduceTaskChat(state, {
      type: "chunk",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-1",
      blockId: "text-1",
      blockOffset: 0,
      offset: 0,
      content: "hello",
    });
    const once = state;
    state = reduceTaskChat(state, {
      type: "chunk",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-1",
      blockId: "text-1",
      blockOffset: 0,
      offset: 0,
      content: "hello again",
    });

    expect(selectBlocks(state, 9)).toEqual([
      expect.objectContaining({ id: "tool-1", tool_output: "found", status: "done" }),
      expect.objectContaining({ id: "text-1", content: "hello" }),
    ]);
    expect(state).toBe(once);
    expect(initial.messages).toEqual([]);
    expect(beforeUpdate.messages[0]?.blocks[0]).toEqual(
      expect.objectContaining({ id: "tool-1", status: "pending" }),
    );
  });

  it("uses UTF-16 offsets and requests a resync for a future or missing offset", () => {
    let state = initialTaskChatState(7);
    state = reduceTaskChat(state, {
      type: "start",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-1",
    });
    state = reduceTaskChat(state, {
      type: "block-created",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-1",
      block: textBlock("text-1"),
    });
    state = reduceTaskChat(state, {
      type: "chunk",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-1",
      blockId: "text-1",
      blockOffset: 0,
      offset: 0,
      content: "😀",
    });
    state = reduceTaskChat(state, {
      type: "chunk",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-1",
      blockId: "text-1",
      blockOffset: 2,
      offset: 2,
      content: "好",
    });
    expect(selectBlocks(state, 9)[0]).toEqual(
      expect.objectContaining({ content: "😀好" }),
    );

    const duplicate = reduceTaskChat(state, {
      type: "chunk",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-1",
      blockId: "text-1",
      blockOffset: 1,
      offset: 1,
      content: "corrupt-overlap",
    });
    expect(duplicate).toBe(state);

    const future = reduceTaskChat(state, {
      type: "chunk",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-1",
      blockId: "text-1",
      blockOffset: 4,
      offset: 4,
      content: "lost",
    });
    expect(future.needsResync).toBe(true);
    expect(selectBlocks(future, 9)[0]).toEqual(
      expect.objectContaining({ content: "😀好" }),
    );
  });

  it("isolates task, subtask, and generation events", () => {
    let state = initialTaskChatState(7);
    state = reduceTaskChat(state, {
      type: "start",
      taskId: 7,
      subtaskId: 9,
      generationId: "current",
    });
    state = reduceTaskChat(state, {
      type: "block-created",
      taskId: 7,
      subtaskId: 9,
      generationId: "current",
      block: textBlock("text-1"),
    });
    const current = state;

    for (const action of [
      { ...chunkAction(), taskId: 8 },
      { ...chunkAction(), subtaskId: 10 },
      { ...chunkAction(), generationId: "stale" },
    ] as const) {
      state = reduceTaskChat(state, action);
      expect(state).toBe(current);
    }
  });

  it("hydrates durable blocks, expands messages_chain, and preserves terminal states", () => {
    const subtasks: Subtask[] = [
      subtask({ id: 1, role: "USER", prompt: "question", message_id: 4 }),
      subtask({
        id: 2,
        role: "ASSISTANT",
        message_id: 5,
        result: {
          value: "answer",
          blocks: [{ ...textBlock("persisted", "answer"), status: "done" }],
          messages_chain: [{ role: "assistant", content: "answer" }],
          context_compactions: [],
          sources: [],
          termination_reason: null,
        },
      }),
      subtask({
        id: 3,
        role: "ASSISTANT",
        message_id: 6,
        status: "FAILED",
        error_message: "provider_call_failed",
        result: {
          value: "partial",
          messages_chain: [
            {
              role: "assistant",
              content: null,
              tool_calls: [
                {
                  id: "call-1",
                  type: "function",
                  function: { name: "lookup", arguments: "{\"q\":\"safe\"}" },
                },
              ],
            },
            {
              role: "tool",
              tool_call_id: "call-1",
              name: "lookup",
              content: "found",
            },
            { role: "assistant", content: "partial" },
          ],
        },
      }),
      subtask({
        id: 4,
        role: "ASSISTANT",
        message_id: 7,
        status: "CANCELLED",
        result: { value: "stopped", messages_chain: [] },
      }),
    ];

    const state = reduceTaskChat(initialTaskChatState(7), {
      type: "hydrate",
      taskId: 7,
      subtasks,
      streaming: null,
      replace: true,
    });

    expect(state.messages.map(({ role, status }) => [role, status])).toEqual([
      ["USER", "COMPLETED"],
      ["ASSISTANT", "COMPLETED"],
      ["ASSISTANT", "FAILED"],
      ["ASSISTANT", "CANCELLED"],
    ]);
    expect(selectBlocks(state, 2)).toEqual([
      expect.objectContaining({ id: "persisted", content: "answer" }),
    ]);
    expect(selectBlocks(state, 3)).toEqual([
      expect.objectContaining({ type: "tool", tool_name: "lookup", tool_output: "found" }),
      expect.objectContaining({ type: "text", content: "partial" }),
    ]);
    expect(selectBlocks(state, 4)).toEqual([
      expect.objectContaining({ type: "text", content: "stopped" }),
    ]);
    expect(maxDurableMessageId(state)).toBe(7);
  });

  it("applies an active snapshot authoritatively and terminalizes with done", () => {
    const running = subtask({
      id: 9,
      role: "ASSISTANT",
      message_id: 12,
      status: "RUNNING",
      progress: 30,
      completed_at: null,
    });
    const streaming: ActiveStreamSnapshot = {
      task_id: 7,
      subtask_id: 9,
      generation_id: "gen-active",
      offset: 2,
      cached_content: "😀",
      blocks: [textBlock("text-1")],
      started_at: timestamp,
      last_activity_at: timestamp,
      status_updated: { phase: "thinking" },
    };
    let state = reduceTaskChat(initialTaskChatState(7), {
      type: "hydrate",
      taskId: 7,
      subtasks: [running],
      streaming,
      replace: true,
    });
    expect(state.messages[0]).toEqual(
      expect.objectContaining({ generationId: "gen-active", streamOffset: 2 }),
    );
    expect(selectBlocks(state, 9)[0]).toEqual(
      expect.objectContaining({ id: "text-1", content: "😀" }),
    );
    expect(state.statusUpdated).toEqual({ phase: "thinking" });

    state = reduceTaskChat(state, {
      type: "done",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-active",
      result: {
        value: "final",
        blocks: [{ ...textBlock("final", "final"), status: "done" }],
        messages_chain: [{ role: "assistant", content: "final" }],
        context_compactions: [],
        sources: [],
        termination_reason: null,
      },
    });
    expect(state.messages[0]).toEqual(
      expect.objectContaining({ status: "COMPLETED", generationId: null }),
    );
    expect(selectBlocks(state, 9)).toEqual([
      expect.objectContaining({ id: "final", content: "final", status: "done" }),
    ]);
  });

  it("rebuilds the live text block from the real cached UTF-16 snapshot", () => {
    const running = subtask({
      id: 9,
      role: "ASSISTANT",
      message_id: 12,
      status: "RUNNING",
      completed_at: null,
    });
    let state = reduceTaskChat(initialTaskChatState(7), {
      type: "hydrate",
      taskId: 7,
      subtasks: [running],
      streaming: {
        task_id: 7,
        subtask_id: 9,
        generation_id: "gen-real",
        offset: 5,
        cached_content: "前😀后来",
        blocks: [
          { ...textBlock("completed", "前😀"), status: "done" },
          {
            id: "tool-1",
            type: "tool",
            tool_use_id: "call-1",
            tool_name: "lookup",
            tool_input: {},
            tool_output: "found",
            status: "done",
            timestamp,
          },
          textBlock("current", "后"),
        ],
        started_at: timestamp,
        last_activity_at: timestamp,
        status_updated: null,
      },
      replace: true,
    });
    expect(state.needsResync).toBe(false);
    expect(selectBlocks(state, 9)).toEqual([
      expect.objectContaining({ id: "completed", content: "前😀", status: "done" }),
      expect.objectContaining({ id: "tool-1", tool_output: "found" }),
      expect.objectContaining({ id: "current", content: "后来", status: "streaming" }),
    ]);

    state = reduceTaskChat(state, {
      type: "chunk",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-real",
      blockId: "current",
      blockOffset: 2,
      offset: 5,
      content: "😀",
    });
    expect(state.needsResync).toBe(false);
    expect(selectBlocks(state, 9)[2]).toEqual(
      expect.objectContaining({ id: "current", content: "后来😀" }),
    );
    expect(state.messages[0]?.streamOffset).toBe(7);
  });

  it("fails safe when cached snapshot text has no authoritative block ID", () => {
    const state = reduceTaskChat(initialTaskChatState(7), {
      type: "hydrate",
      taskId: 7,
      subtasks: [],
      streaming: {
        task_id: 7,
        subtask_id: 9,
        generation_id: "gen-malformed",
        offset: 2,
        cached_content: "ok",
        blocks: [],
        started_at: timestamp,
        last_activity_at: timestamp,
        status_updated: null,
      },
      replace: true,
    });
    expect(state.needsResync).toBe(true);
    expect(selectBlocks(state, 9)).toEqual([]);
  });

  it("keeps partial blocks and distinct failed/cancelled terminal states", () => {
    let state = reduceTaskChat(initialTaskChatState(7), {
      type: "start",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-error",
    });
    state = reduceTaskChat(state, {
      type: "error",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-error",
      code: "provider_call_failed",
      result: {
        value: "partial",
        blocks: [{ ...textBlock("partial", "partial"), status: "done" }],
        context_compactions: [],
        sources: [],
        termination_reason: null,
      },
    });
    expect(state.messages[0]).toEqual(
      expect.objectContaining({ status: "FAILED", errorCode: "provider_call_failed" }),
    );
    expect(selectBlocks(state, 9)).toEqual([
      expect.objectContaining({ content: "partial" }),
    ]);

    state = reduceTaskChat(state, {
      type: "start",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-cancel",
    });
    state = reduceTaskChat(state, {
      type: "cancelled",
      taskId: 7,
      subtaskId: 9,
      generationId: "gen-cancel",
      result: null,
    });
    expect(state.messages[0]).toEqual(
      expect.objectContaining({ status: "CANCELLED", errorCode: null }),
    );
  });

  it("uses the last stable message as reconnect cursor", () => {
    let state = reduceTaskChat(initialTaskChatState(7), {
      type: "hydrate",
      taskId: 7,
      subtasks: [
        subtask({ id: 1, role: "USER", message_id: 5 }),
        subtask({
          id: 2,
          role: "ASSISTANT",
          message_id: 6,
          status: "RUNNING",
          completed_at: null,
        }),
      ],
      streaming: null,
      replace: true,
    });
    state = reduceTaskChat(state, {
      type: "optimistic-user",
      localKey: "optimistic",
      taskId: 7,
      prompt: "not durable",
    });
    expect(maxDurableMessageId(state)).toBe(6);
    expect(reconnectCursorMessageId(state)).toBe(5);

    state = reduceTaskChat(state, {
      type: "hydrate",
      taskId: 7,
      subtasks: [
        subtask({ id: 1, role: "USER", message_id: 5 }),
        subtask({ id: 2, role: "ASSISTANT", message_id: 6 }),
      ],
      streaming: null,
      replace: true,
    });
    expect(reconnectCursorMessageId(state)).toBe(6);
  });

  it("does not let a retry rollback overwrite a started generation", () => {
    const failed = subtask({
      id: 6,
      role: "ASSISTANT",
      message_id: 6,
      status: "FAILED",
      error_message: "provider_call_failed",
      result: { value: "old", messages_chain: [] },
    });
    let state = reduceTaskChat(initialTaskChatState(7), {
      type: "hydrate",
      taskId: 7,
      subtasks: [failed],
      streaming: null,
      replace: true,
    });
    const snapshot = state.messages[0]!;
    state = reduceTaskChat(state, {
      type: "retry-pending",
      taskId: 7,
      subtaskId: 6,
    });
    state = reduceTaskChat(state, {
      type: "start",
      taskId: 7,
      subtaskId: 6,
      generationId: "accepted-generation",
    });
    state = reduceTaskChat(state, {
      type: "retry-restore",
      taskId: 7,
      subtaskId: 6,
      snapshot,
    });
    expect(state.messages[0]).toEqual(
      expect.objectContaining({
        status: "RUNNING",
        generationId: "accepted-generation",
        errorMessage: null,
      }),
    );
  });
});

function chunkAction() {
  return {
    type: "chunk" as const,
    taskId: 7,
    subtaskId: 9,
    generationId: "current",
    blockId: "text-1",
    blockOffset: 0,
    offset: 0,
    content: "data",
  };
}
