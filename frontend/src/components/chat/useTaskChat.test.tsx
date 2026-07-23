import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SocketContextValue } from "@/contexts/SocketContext";
import type {
  ServerToClientEvents,
  SocketEventHandlers,
  TaskJoinAck,
} from "@/lib/socket-types";
import type { Subtask, SubtaskContextBrief } from "@/lib/types";

import { useTaskChat } from "./useTaskChat";

const socket = vi.hoisted(() => ({
  connected: true,
  handlers: {} as SocketEventHandlers,
  reconnect: null as (() => void) | null,
  cleanupHandlers: vi.fn(),
  cleanupReconnect: vi.fn(),
  joinTask: vi.fn(),
  leaveTask: vi.fn(),
  sendChatMessage: vi.fn(),
  cancel: vi.fn(),
  retry: vi.fn(),
  registerHandlers: vi.fn((handlers: SocketEventHandlers) => {
    socket.handlers = handlers;
    return socket.cleanupHandlers;
  }),
  onReconnect: vi.fn((callback: () => void) => {
    socket.reconnect = callback;
    return socket.cleanupReconnect;
  }),
}));

vi.mock("@/contexts/SocketContext", () => ({
  useSocket: (): SocketContextValue => ({
    connected: socket.connected,
    joinTask: socket.joinTask,
    leaveTask: socket.leaveTask,
    sendChatMessage: socket.sendChatMessage,
    cancel: socket.cancel,
    retry: socket.retry,
    registerHandlers: socket.registerHandlers,
    onReconnect: socket.onReconnect,
  }),
}));

const timestamp = "2026-07-22T00:00:00Z";

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

function emit<K extends keyof ServerToClientEvents>(
  event: K,
  payload: Parameters<ServerToClientEvents[K]>[0],
) {
  const handler = socket.handlers[event] as ((value: typeof payload) => void) | undefined;
  act(() => handler?.(payload));
}

describe("useTaskChat", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    socket.connected = true;
    socket.handlers = {};
    socket.reconnect = null;
    socket.joinTask.mockResolvedValue({ task_id: 7, subtasks: [], streaming: null });
    socket.leaveTask.mockResolvedValue({ task_id: 7 });
    socket.cancel.mockResolvedValue({ task_id: 7, subtask_id: 9, accepted: true });
    socket.retry.mockResolvedValue({ task_id: 7, subtask_id: 9 });
  });

  it("joins, hydrates history plus active stream, and cleans every subscription", async () => {
    socket.joinTask.mockResolvedValue({
      task_id: 7,
      subtasks: [subtask({ id: 1, role: "USER", prompt: "hello", message_id: 4 })],
      streaming: {
        task_id: 7,
        subtask_id: 9,
        generation_id: "gen-1",
        offset: 2,
        cached_content: "ok",
        blocks: [
          {
            id: "text-1",
            type: "text",
            content: "",
            status: "streaming",
            timestamp,
          },
        ],
        started_at: timestamp,
        last_activity_at: timestamp,
        status_updated: null,
      },
    });
    const { result, unmount } = renderHook(() => useTaskChat(7));

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(socket.joinTask).toHaveBeenCalledWith(7, { afterMessageId: null });
    expect(result.current.messages).toEqual([
      expect.objectContaining({ role: "USER", prompt: "hello", messageId: 4 }),
      expect.objectContaining({ subtaskId: 9, generationId: "gen-1" }),
    ]);
    expect(result.current.messages[1]?.blocks[0]).toEqual(
      expect.objectContaining({ id: "text-1", content: "ok" }),
    );

    unmount();
    expect(socket.cleanupHandlers).toHaveBeenCalledTimes(1);
    expect(socket.cleanupReconnect).toHaveBeenCalledTimes(1);
    expect(socket.leaveTask).toHaveBeenCalledWith(7);
  });

  it("waits for the first connection, then reconnects without resetting durable history", async () => {
    socket.connected = false;
    socket.joinTask
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [subtask({ id: 1, role: "USER", message_id: 6, prompt: "kept" })],
        streaming: null,
      })
      .mockResolvedValueOnce({ task_id: 7, subtasks: [], streaming: null });
    const { result, rerender } = renderHook(() => useTaskChat(7));

    await act(async () => Promise.resolve());
    expect(socket.joinTask).not.toHaveBeenCalled();
    expect(result.current.errorCode).toBeNull();
    expect(result.current.loading).toBe(true);

    socket.connected = true;
    rerender();
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(socket.joinTask).toHaveBeenCalledTimes(1);
    expect(socket.joinTask).toHaveBeenLastCalledWith(7, { afterMessageId: null });
    expect(result.current.messages[0]).toEqual(
      expect.objectContaining({ prompt: "kept", messageId: 6 }),
    );

    socket.connected = false;
    rerender();
    expect(result.current.messages).toHaveLength(1);
    socket.connected = true;
    rerender();
    act(() => socket.reconnect?.());
    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(2));
    expect(socket.joinTask).toHaveBeenLastCalledWith(7, {
      afterMessageId: 6,
      force: true,
    });
    expect(result.current.messages[0]).toEqual(
      expect.objectContaining({ prompt: "kept", messageId: 6 }),
    );
  });

  it("reports an initial join failure safely and removes handlers without a false leave", async () => {
    socket.joinTask.mockRejectedValue(Object.assign(new Error("private"), {
      code: "task_not_found",
    }));
    const { result, unmount } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.errorCode).toBe("task_not_found");

    unmount();
    expect(socket.cleanupHandlers).toHaveBeenCalledTimes(1);
    expect(socket.cleanupReconnect).toHaveBeenCalledTimes(1);
    expect(socket.leaveTask).not.toHaveBeenCalled();
  });

  it("discards a failed hydration attempt before replaying an authoritative full join", async () => {
    let rejectJoin!: (error: unknown) => void;
    socket.leaveTask.mockRejectedValueOnce(Object.assign(new Error("cleanup failed"), {
      code: "socket_disconnected",
    }));
    socket.joinTask
      .mockReturnValueOnce(new Promise((_, reject) => {
        rejectJoin = reject;
      }))
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [subtask({
          id: 9,
          role: "ASSISTANT",
          status: "COMPLETED",
          result: {
            value: "authoritative answer",
            blocks: [{
              id: "authoritative-text",
              type: "text",
              content: "authoritative answer",
              status: "done",
              timestamp,
            }],
            messages_chain: [{ role: "assistant", content: "authoritative answer" }],
            context_compactions: [],
            sources: [],
            termination_reason: null,
          },
        })],
        streaming: null,
      });
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(1));

    emit("chat:start", {
      task_id: 7,
      subtask_id: 9,
      generation_id: "stale-generation",
      status: "RUNNING",
    });
    emit("task:status", {
      task: {
        id: 7,
        name: "Task",
        href: "/chat?task=7",
        status: "RUNNING",
        agent: { id: null, name: "No agent", is_available: true },
        model_override: null,
        created_at: timestamp,
        updated_at: timestamp,
      },
    });
    await act(async () => {
      rejectJoin(Object.assign(new Error("late join"), { code: "socket_timeout" }));
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.errorCode).toBe("socket_timeout"));
    expect(socket.leaveTask).toHaveBeenCalledWith(7);
    expect(result.current.messages).toEqual([]);
    expect(result.current.taskStatus).toBeNull();

    act(() => socket.reconnect?.());
    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.messages[0]).toEqual(
      expect.objectContaining({ subtaskId: 9, status: "COMPLETED" }),
    ));
    expect(result.current.taskStatus).toBe("COMPLETED");
    expect(result.current.messages[0]?.blocks).toEqual([
      expect.objectContaining({ content: "authoritative answer" }),
    ]);
  });

  it("fails closed on a mismatched join ACK and never replays its queued lifecycle", async () => {
    let resolveMismatchedJoin!: (value: TaskJoinAck) => void;
    socket.joinTask
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveMismatchedJoin = resolve;
      }))
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [subtask({
          id: 9,
          role: "ASSISTANT",
          status: "COMPLETED",
          result: {
            value: "database result",
            blocks: [{
              id: "database-text",
              type: "text",
              content: "database result",
              status: "done",
              timestamp,
            }],
            messages_chain: [{ role: "assistant", content: "database result" }],
            context_compactions: [],
            sources: [],
            termination_reason: null,
          },
        })],
        streaming: null,
      });
    const { result } = renderHook(() => useTaskChat(7));
    emit("chat:start", {
      task_id: 7,
      subtask_id: 9,
      generation_id: "stale-generation",
      status: "RUNNING",
    });
    await act(async () => {
      resolveMismatchedJoin({ task_id: 8, subtasks: [], streaming: null });
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.errorCode).toBe("malformed_ack"));
    expect(socket.leaveTask).toHaveBeenCalledWith(7);
    expect(result.current.messages).toEqual([]);
    expect(result.current.taskStatus).toBeNull();

    act(() => socket.reconnect?.());
    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.messages[0]).toEqual(
      expect.objectContaining({ subtaskId: 9, status: "COMPLETED" }),
    ));
    expect(result.current.taskStatus).toBe("COMPLETED");
    expect(result.current.messages[0]?.blocks).toEqual([
      expect.objectContaining({ content: "database result" }),
    ]);
  });

  it("leaves a room when its join ACK loses the cleanup race", async () => {
    let resolveJoin!: (value: { task_id: number; subtasks: Subtask[]; streaming: null }) => void;
    socket.joinTask.mockReturnValue(
      new Promise((resolve) => {
        resolveJoin = resolve;
      }),
    );
    const { unmount } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(1));
    unmount();

    await act(async () => {
      resolveJoin({ task_id: 7, subtasks: [], streaming: null });
      await Promise.resolve();
    });
    expect(socket.leaveTask).toHaveBeenCalledWith(7);
  });

  it("does not let a stale failed join leave the same Task owned by a newer lifecycle", async () => {
    let rejectFirstJoin!: (error: unknown) => void;
    socket.joinTask
      .mockReturnValueOnce(new Promise((_, reject) => {
        rejectFirstJoin = reject;
      }))
      .mockResolvedValueOnce({ task_id: 8, subtasks: [], streaming: null })
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [subtask({
          id: 9,
          role: "ASSISTANT",
          status: "COMPLETED",
          result: { value: "new lifecycle", messages_chain: [] },
        })],
        streaming: null,
      });
    const { result, rerender } = renderHook(
      ({ taskId }: { taskId: number }) => useTaskChat(taskId),
      { initialProps: { taskId: 7 } },
    );
    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(1));

    rerender({ taskId: 8 });
    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.loading).toBe(false));
    rerender({ taskId: 7 });
    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(result.current.messages[0]).toEqual(
      expect.objectContaining({ subtaskId: 9, status: "COMPLETED" }),
    ));

    await act(async () => {
      rejectFirstJoin(Object.assign(new Error("stale timeout"), {
        code: "socket_timeout",
      }));
      await Promise.resolve();
    });

    expect(socket.leaveTask).not.toHaveBeenCalledWith(7);
    expect(result.current.errorCode).toBeNull();
    expect(result.current.messages[0]).toEqual(
      expect.objectContaining({ subtaskId: 9, status: "COMPLETED" }),
    );
  });

  it("queues live events until join hydration and isolates wrong envelopes", async () => {
    let resolveJoin!: (value: { task_id: number; subtasks: Subtask[]; streaming: null }) => void;
    socket.joinTask.mockReturnValue(
      new Promise((resolve) => {
        resolveJoin = resolve;
      }),
    );
    const { result } = renderHook(() => useTaskChat(7));

    emit("chat:start", {
      task_id: 8,
      subtask_id: 99,
      generation_id: "wrong-task",
      status: "RUNNING",
    });
    emit("chat:start", {
      task_id: 7,
      subtask_id: 9,
      generation_id: "gen-1",
      status: "RUNNING",
    });
    expect(result.current.messages).toEqual([]);

    await act(async () => {
      resolveJoin({
        task_id: 7,
        subtasks: [subtask({ id: 1, role: "USER", prompt: "durable" })],
        streaming: null,
      });
      await Promise.resolve();
    });
    expect(result.current.messages).toEqual([
      expect.objectContaining({ subtaskId: 1 }),
      expect.objectContaining({ subtaskId: 9, generationId: "gen-1" }),
    ]);

    emit("chat:chunk", {
      task_id: 7,
      subtask_id: 9,
      generation_id: "stale",
      block_id: "text-1",
      block_offset: 0,
      offset: 0,
      content: "bad",
    });
    expect(result.current.messages[1]?.blocks).toEqual([]);
  });

  it("replays a terminal Task status after an older RUNNING join snapshot", async () => {
    let resolveJoin!: (value: TaskJoinAck) => void;
    socket.joinTask
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveJoin = resolve;
      }))
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [subtask({
          id: 9,
          role: "ASSISTANT",
          status: "COMPLETED",
          result: {
            value: "authoritative answer",
            blocks: [{
              id: "authoritative-text",
              type: "text",
              content: "authoritative answer",
              status: "done",
              timestamp,
            }],
            messages_chain: [{ role: "assistant", content: "authoritative answer" }],
            context_compactions: [],
            sources: [],
            termination_reason: null,
          },
        })],
        streaming: null,
      });
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(1));

    emit("task:status", {
      task: {
        id: 7,
        name: "Task",
        href: "/chat?task=7",
        status: "COMPLETED",
        agent: { id: null, name: "No agent", is_available: true },
        model_override: null,
        created_at: timestamp,
        updated_at: timestamp,
      },
    });
    await act(async () => {
      resolveJoin({
        task_id: 7,
        subtasks: [subtask({
          id: 9,
          role: "ASSISTANT",
          status: "RUNNING",
          completed_at: null,
        })],
        streaming: null,
      });
      await Promise.resolve();
    });

    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.messages[0]).toEqual(
      expect.objectContaining({ subtaskId: 9, status: "COMPLETED" }),
    ));
    expect(result.current.taskStatus).toBe("COMPLETED");
    expect(result.current.messages[0]?.blocks).toEqual(
      [expect.objectContaining({ content: "authoritative answer" })],
    );
  });

  it("replays chat:done after an older RUNNING join snapshot", async () => {
    let resolveJoin!: (value: TaskJoinAck) => void;
    socket.joinTask.mockReturnValue(new Promise((resolve) => {
      resolveJoin = resolve;
    }));
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(1));
    emit("chat:done", {
      task_id: 7,
      subtask_id: 9,
      generation_id: "gen-race",
      result: {
        value: "terminal answer",
        blocks: [{
          id: "terminal-text",
          type: "text",
          content: "terminal answer",
          status: "done",
          timestamp,
        }],
        messages_chain: [{ role: "assistant", content: "terminal answer" }],
        context_compactions: [],
        sources: [],
        termination_reason: null,
      },
    });
    await act(async () => {
      resolveJoin({
        task_id: 7,
        subtasks: [subtask({
          id: 9,
          role: "ASSISTANT",
          status: "RUNNING",
          completed_at: null,
        })],
        streaming: {
          task_id: 7,
          subtask_id: 9,
          generation_id: "gen-race",
          offset: 0,
          cached_content: "",
          blocks: [],
          started_at: timestamp,
          last_activity_at: timestamp,
          status_updated: null,
        },
      });
      await Promise.resolve();
    });

    expect(result.current.taskStatus).toBe("COMPLETED");
    expect(result.current.messages[0]).toEqual(
      expect.objectContaining({ subtaskId: 9, status: "COMPLETED" }),
    );
    expect(result.current.messages[0]?.blocks).toEqual([
      expect.objectContaining({ id: "terminal-text", content: "terminal answer" }),
    ]);
  });

  it("reconnects with the current maximum durable cursor and force joins", async () => {
    socket.joinTask
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [
          subtask({ id: 1, role: "USER", message_id: 5 }),
          subtask({ id: 2, role: "ASSISTANT", message_id: 6 }),
        ],
        streaming: null,
      })
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [subtask({ id: 3, role: "USER", message_id: 7, prompt: "new" })],
        streaming: null,
      });
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));

    act(() => socket.reconnect?.());
    await waitFor(() =>
      expect(socket.joinTask).toHaveBeenLastCalledWith(7, {
        afterMessageId: 6,
        force: true,
      }),
    );
    await waitFor(() => expect(result.current.messages).toHaveLength(3));
    expect(result.current.messages[2]).toEqual(expect.objectContaining({ messageId: 7 }));
  });

  it("backs the reconnect cursor before an in-place running Assistant", async () => {
    socket.joinTask
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [
          subtask({ id: 1, role: "USER", message_id: 5, prompt: "question" }),
          subtask({
            id: 2,
            role: "ASSISTANT",
            message_id: 6,
            status: "RUNNING",
            completed_at: null,
          }),
        ],
        streaming: {
          task_id: 7,
          subtask_id: 2,
          generation_id: "gen-running",
          offset: 7,
          cached_content: "partial",
          blocks: [
            {
              id: "live-text",
              type: "text",
              content: "",
              status: "streaming",
              timestamp,
            },
          ],
          started_at: timestamp,
          last_activity_at: timestamp,
          status_updated: null,
        },
      })
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [
          subtask({
            id: 2,
            role: "ASSISTANT",
            message_id: 6,
            status: "COMPLETED",
            result: {
              value: "final answer",
              blocks: [
                {
                  id: "final-text",
                  type: "text",
                  content: "final answer",
                  status: "done",
                  timestamp,
                },
              ],
              messages_chain: [{ role: "assistant", content: "final answer" }],
              context_compactions: [],
              sources: [],
              termination_reason: null,
            },
          }),
        ],
        streaming: null,
      });
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.messages[1]).toEqual(
      expect.objectContaining({ messageId: 6, status: "RUNNING" }),
    );
    expect(result.current.taskStatus).toBe("RUNNING");

    act(() => socket.reconnect?.());
    await waitFor(() =>
      expect(socket.joinTask).toHaveBeenLastCalledWith(7, {
        afterMessageId: 5,
        force: true,
      }),
    );
    await waitFor(() =>
      expect(result.current.messages[1]).toEqual(
        expect.objectContaining({ messageId: 6, status: "COMPLETED" }),
      ),
    );
    expect(result.current.taskStatus).toBe("COMPLETED");
    expect(result.current.messages[1]?.blocks).toEqual([
      expect.objectContaining({ id: "final-text", content: "final answer" }),
    ]);
  });

  it("reconciles an optimistic user with the durable send ACK", async () => {
    let resolveSend!: (value: { task_id: number; subtask_id: number; message_id: number }) => void;
    socket.sendChatMessage.mockReturnValue(
      new Promise((resolve) => {
        resolveSend = resolve;
      }),
    );
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let sendPromise!: Promise<unknown>;
    act(() => {
      sendPromise = result.current.send("question", { contextIds: [3] });
    });
    expect(result.current.messages).toEqual([
      expect.objectContaining({ role: "USER", prompt: "question", optimistic: true }),
    ]);
    expect(socket.sendChatMessage).toHaveBeenCalledWith({
      task_id: 7,
      message: "question",
      context_ids: [3],
    });

    await act(async () => {
      resolveSend({ task_id: 7, subtask_id: 10, message_id: 4 });
      await sendPromise;
    });
    expect(result.current.messages).toEqual([
      expect.objectContaining({
        subtaskId: 10,
        messageId: 4,
        optimistic: false,
        status: "COMPLETED",
      }),
    ]);
  });

  it("removes a failed optimistic send and exposes only a stable error code", async () => {
    socket.sendChatMessage.mockRejectedValue(Object.assign(new Error("secret"), { code: "task_running" }));
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await expect(result.current.send("private prompt")).rejects.toMatchObject({
        code: "task_running",
      });
    });
    expect(result.current.messages).toEqual([]);
    expect(result.current.errorCode).toBe("task_running");
  });

  it("adopts a new Task ACK before replaying its events", async () => {
    socket.sendChatMessage.mockResolvedValue({ task_id: 12, subtask_id: 20, message_id: 1 });
    const context: SubtaskContextBrief = {
      id: 31,
      context_type: "attachment",
      name: "source.txt",
      status: "ready",
      mime_type: "text/plain",
      file_extension: ".txt",
      file_size: 6,
      text_length: 6,
      type_data: {},
    };
    const { result } = renderHook(() => useTaskChat(null));

    await act(async () => {
      await result.current.send("first", {
        agentId: "agent-1",
        contextIds: [31],
        contexts: [context],
      });
    });
    expect(socket.sendChatMessage).toHaveBeenCalledWith({
      task_id: null,
      message: "first",
      agent_id: "agent-1",
      context_ids: [31],
    });
    expect(result.current.taskId).toBe(12);
    expect(result.current.createdTaskId).toBe(12);
    expect(result.current.taskStatus).toBe("PENDING");
    expect(result.current.messages[0]).toEqual(
      expect.objectContaining({ subtaskId: 20, messageId: 1, taskId: 12 }),
    );
    expect(result.current.messages[0]?.contexts).toEqual([context]);

    emit("task:status", {
      task: {
        id: 12,
        name: "New Task",
        href: "/chat?task=12",
        status: "COMPLETED",
        agent: { id: "agent-1", name: "Agent", is_available: true },
        model_override: null,
        created_at: timestamp,
        updated_at: timestamp,
      },
    });
    expect(result.current.taskStatus).toBe("COMPLETED");
  });

  it("holds a completed Task in PENDING after send ACK until lifecycle events advance it", async () => {
    socket.sendChatMessage.mockResolvedValue({ task_id: 7, subtask_id: 21, message_id: 11 });
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));
    emit("task:status", {
      task: {
        id: 7,
        name: "Existing Task",
        href: "/chat?task=7",
        status: "COMPLETED",
        agent: { id: null, name: "No agent", is_available: true },
        model_override: null,
        created_at: timestamp,
        updated_at: timestamp,
      },
    });
    expect(result.current.taskStatus).toBe("COMPLETED");

    await act(async () => {
      await result.current.send("next");
    });
    expect(result.current.taskStatus).toBe("PENDING");

    emit("chat:start", {
      task_id: 7,
      subtask_id: 22,
      generation_id: "gen-next",
      status: "RUNNING",
    });
    expect(result.current.taskStatus).toBe("RUNNING");
    emit("task:status", {
      task: {
        id: 7,
        name: "Existing Task",
        href: "/chat?task=7",
        status: "FAILED",
        agent: { id: null, name: "No agent", is_available: true },
        model_override: null,
        created_at: timestamp,
        updated_at: timestamp,
      },
    });
    expect(result.current.taskStatus).toBe("FAILED");
  });

  it("resyncs a failed Assistant that became terminal before chat:start", async () => {
    socket.sendChatMessage.mockResolvedValue({
      task_id: 7,
      subtask_id: 10,
      message_id: 5,
    });
    socket.joinTask
      .mockResolvedValueOnce({ task_id: 7, subtasks: [], streaming: null })
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [
          subtask({
            id: 10,
            role: "USER",
            message_id: 5,
            prompt: "question",
          }),
          subtask({
            id: 11,
            role: "ASSISTANT",
            message_id: 6,
            parent_id: 5,
            status: "FAILED",
            error_message: "stream_store_start_failed",
            result: { value: "", messages_chain: [] },
          }),
        ],
        streaming: null,
      });
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.send("question");
    });
    emit("task:status", {
      task: {
        id: 7,
        name: "Task",
        href: "/chat?task=7",
        status: "FAILED",
        agent: { id: null, name: "No agent", is_available: true },
        model_override: null,
        created_at: timestamp,
        updated_at: timestamp,
      },
    });

    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(2));
    expect(socket.joinTask).toHaveBeenLastCalledWith(7, {
      afterMessageId: null,
      force: true,
    });
    await waitFor(() => expect(result.current.messages[1]).toEqual(
      expect.objectContaining({
        subtaskId: 11,
        parentId: 5,
        status: "FAILED",
        errorMessage: "stream_store_start_failed",
      }),
    ));
    expect(result.current.taskStatus).toBe("FAILED");

    await act(async () => {
      await result.current.retryAssistant(11);
    });
    expect(socket.retry).toHaveBeenCalledWith(7, 11);
  });

  it("clears the awaiting lifecycle on chat:start without an extra terminal resync", async () => {
    socket.sendChatMessage.mockResolvedValue({
      task_id: 7,
      subtask_id: 10,
      message_id: 5,
    });
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      await result.current.send("question");
    });

    emit("chat:start", {
      task_id: 7,
      subtask_id: 11,
      generation_id: "normal-generation",
      status: "RUNNING",
    });
    emit("chat:error", {
      task_id: 7,
      subtask_id: 11,
      generation_id: "normal-generation",
      code: "provider_call_failed",
      result: null,
    });
    await act(async () => Promise.resolve());

    expect(socket.joinTask).toHaveBeenCalledTimes(1);
    expect(result.current.messages[1]).toEqual(
      expect.objectContaining({ subtaskId: 11, status: "FAILED" }),
    );
    expect(result.current.taskStatus).toBe("FAILED");
  });

  it("resyncs chat:cancelled without chat:start or a Task status event", async () => {
    socket.sendChatMessage.mockResolvedValue({
      task_id: 7,
      subtask_id: 10,
      message_id: 5,
    });
    socket.joinTask
      .mockResolvedValueOnce({ task_id: 7, subtasks: [], streaming: null })
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [
          subtask({
            id: 10,
            role: "USER",
            message_id: 5,
            prompt: "cancel me",
          }),
          subtask({
            id: 11,
            role: "ASSISTANT",
            message_id: 6,
            parent_id: 5,
            status: "CANCELLED",
            result: { value: "", messages_chain: [] },
          }),
        ],
        streaming: null,
      });
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      await result.current.send("cancel me");
    });

    emit("chat:cancelled", {
      task_id: 7,
      subtask_id: 11,
      generation_id: "cancelled-before-start",
      result: null,
    });

    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(2));
    expect(socket.joinTask).toHaveBeenLastCalledWith(7, {
      afterMessageId: null,
      force: true,
    });
    await waitFor(() => expect(result.current.messages[1]).toEqual(
      expect.objectContaining({
        subtaskId: 11,
        parentId: 5,
        status: "CANCELLED",
      }),
    ));
    expect(result.current.taskStatus).toBe("CANCELLED");
  });

  it("recovers after a terminal event with the wrong active generation", async () => {
    socket.sendChatMessage.mockResolvedValue({
      task_id: 7,
      subtask_id: 10,
      message_id: 5,
    });
    socket.joinTask
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [
          subtask({ id: 1, role: "USER", message_id: 1, prompt: "old" }),
          subtask({
            id: 2,
            role: "ASSISTANT",
            message_id: 2,
            parent_id: 1,
            status: "RUNNING",
            completed_at: null,
          }),
        ],
        streaming: null,
      })
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [
          subtask({ id: 10, role: "USER", message_id: 5, prompt: "question" }),
          subtask({
            id: 11,
            role: "ASSISTANT",
            message_id: 6,
            parent_id: 5,
            status: "CANCELLED",
            result: { value: "", messages_chain: [] },
          }),
        ],
        streaming: null,
      });
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));
    await act(async () => {
      await result.current.send("question");
    });
    emit("chat:start", {
      task_id: 7,
      subtask_id: 2,
      generation_id: "active-generation",
      status: "RUNNING",
    });
    emit("chat:cancelled", {
      task_id: 7,
      subtask_id: 2,
      generation_id: "wrong-generation",
      result: null,
    });
    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledTimes(2));
    expect(socket.joinTask).toHaveBeenLastCalledWith(7, {
      afterMessageId: null,
      force: true,
    });
    await waitFor(() => expect(result.current.messages[1]).toEqual(
      expect.objectContaining({
        subtaskId: 11,
        parentId: 5,
        status: "CANCELLED",
        generationId: null,
      }),
    ));
  });

  it("replays a new Task terminal status received before its send ACK", async () => {
    let resolveSend!: (value: {
      task_id: number;
      subtask_id: number;
      message_id: number;
    }) => void;
    socket.sendChatMessage.mockReturnValue(new Promise((resolve) => {
      resolveSend = resolve;
    }));
    socket.joinTask.mockResolvedValue({
      task_id: 12,
      subtasks: [
        subtask({
          id: 20,
          task_id: 12,
          role: "USER",
          message_id: 1,
          prompt: "first question",
        }),
        subtask({
          id: 21,
          task_id: 12,
          role: "ASSISTANT",
          message_id: 2,
          parent_id: 1,
          status: "FAILED",
          error_message: "stream_store_start_failed",
          result: { value: "", messages_chain: [] },
        }),
      ],
      streaming: null,
    });
    const { result } = renderHook(() => useTaskChat(null));
    let sendPromise!: Promise<unknown>;
    act(() => {
      sendPromise = result.current.send("first question");
    });

    emit("task:status", {
      task: {
        id: 12,
        name: "New Task",
        href: "/chat?task=12",
        status: "FAILED",
        agent: { id: null, name: "No agent", is_available: true },
        model_override: null,
        created_at: timestamp,
        updated_at: timestamp,
      },
    });
    await act(async () => {
      resolveSend({ task_id: 12, subtask_id: 20, message_id: 1 });
      await sendPromise;
    });

    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledWith(12, {
      afterMessageId: null,
      force: true,
    }));
    await waitFor(() => expect(result.current.messages[1]).toEqual(
      expect.objectContaining({
        subtaskId: 21,
        parentId: 1,
        status: "FAILED",
        errorMessage: "stream_store_start_failed",
      }),
    ));
    expect(result.current.createdTaskId).toBe(12);
    expect(result.current.taskStatus).toBe("FAILED");
  });

  it("replays chat:cancelled received before a new Task send ACK", async () => {
    let resolveSend!: (value: {
      task_id: number;
      subtask_id: number;
      message_id: number;
    }) => void;
    socket.sendChatMessage.mockReturnValue(new Promise((resolve) => {
      resolveSend = resolve;
    }));
    socket.joinTask.mockResolvedValue({
      task_id: 12,
      subtasks: [
        subtask({
          id: 20,
          task_id: 12,
          role: "USER",
          message_id: 1,
          prompt: "cancel first",
        }),
        subtask({
          id: 21,
          task_id: 12,
          role: "ASSISTANT",
          message_id: 2,
          parent_id: 1,
          status: "CANCELLED",
          result: { value: "", messages_chain: [] },
        }),
      ],
      streaming: null,
    });
    const { result } = renderHook(() => useTaskChat(null));
    let sendPromise!: Promise<unknown>;
    act(() => {
      sendPromise = result.current.send("cancel first");
    });
    emit("chat:cancelled", {
      task_id: 12,
      subtask_id: 21,
      generation_id: "cancelled-before-ack",
      result: null,
    });

    await act(async () => {
      resolveSend({ task_id: 12, subtask_id: 20, message_id: 1 });
      await sendPromise;
    });

    await waitFor(() => expect(socket.joinTask).toHaveBeenCalledWith(12, {
      afterMessageId: null,
      force: true,
    }));
    await waitFor(() => expect(result.current.messages[1]).toEqual(
      expect.objectContaining({
        subtaskId: 21,
        parentId: 1,
        status: "CANCELLED",
      }),
    ));
    expect(result.current.taskStatus).toBe("CANCELLED");
  });

  it("retries the failed Assistant in place and resyncs an offset gap from full history", async () => {
    socket.joinTask
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [
          subtask({
            id: 9,
            role: "ASSISTANT",
            status: "FAILED",
            error_message: "provider_call_failed",
          }),
        ],
        streaming: null,
      })
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [subtask({ id: 9, role: "ASSISTANT", status: "RUNNING" })],
        streaming: null,
      });
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));

    await act(async () => {
      await result.current.retryAssistant(9);
    });
    expect(socket.retry).toHaveBeenCalledWith(7, 9);
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]).toEqual(
      expect.objectContaining({ subtaskId: 9, status: "PENDING" }),
    );

    emit("chat:start", {
      task_id: 7,
      subtask_id: 9,
      generation_id: "gen-2",
      status: "RUNNING",
    });
    emit("chat:block_created", {
      task_id: 7,
      subtask_id: 9,
      generation_id: "gen-2",
      block: {
        id: "text-1",
        type: "text",
        content: "",
        status: "streaming",
        timestamp,
      },
    });
    emit("chat:chunk", {
      task_id: 7,
      subtask_id: 9,
      generation_id: "gen-2",
      block_id: "text-1",
      block_offset: 3,
      offset: 3,
      content: "gap",
    });
    await waitFor(() =>
      expect(socket.joinTask).toHaveBeenLastCalledWith(7, {
        afterMessageId: null,
        force: true,
      }),
    );
  });

  it("keeps an uncertain retry pending and recovers the same Assistant on reconnect", async () => {
    socket.retry.mockRejectedValue(Object.assign(new Error("private"), {
      code: "socket_timeout",
    }));
    socket.joinTask
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [
          subtask({ id: 1, role: "USER", message_id: 5, prompt: "question" }),
          subtask({
            id: 6,
            role: "ASSISTANT",
            message_id: 6,
            status: "FAILED",
            error_message: "provider_call_failed",
            result: { value: "old partial", messages_chain: [] },
          }),
        ],
        streaming: null,
      })
      .mockResolvedValueOnce({
        task_id: 7,
        subtasks: [
          subtask({
            id: 6,
            role: "ASSISTANT",
            message_id: 6,
            status: "COMPLETED",
            result: {
              value: "recovered final",
              blocks: [
                {
                  id: "final",
                  type: "text",
                  content: "recovered final",
                  status: "done",
                  timestamp,
                },
              ],
              messages_chain: [{ role: "assistant", content: "recovered final" }],
              context_compactions: [],
              sources: [],
              termination_reason: null,
            },
          }),
        ],
        streaming: null,
      });
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.messages[1]).toEqual(
      expect.objectContaining({ status: "FAILED", subtaskId: 6 }),
    );

    await act(async () => {
      await expect(result.current.retryAssistant(6)).rejects.toMatchObject({
        code: "socket_timeout",
      });
    });
    expect(result.current.errorCode).toBe("socket_timeout");
    expect(result.current.messages[1]).toEqual(
      expect.objectContaining({ status: "PENDING", subtaskId: 6 }),
    );
    expect(result.current.messages[1]?.blocks).toEqual([]);

    act(() => socket.reconnect?.());
    await waitFor(() =>
      expect(socket.joinTask).toHaveBeenLastCalledWith(7, {
        afterMessageId: 5,
        force: true,
      }),
    );
    await waitFor(() =>
      expect(result.current.messages[1]).toEqual(
        expect.objectContaining({ status: "COMPLETED", subtaskId: 6 }),
      ),
    );
    expect(result.current.messages[1]?.blocks).toEqual([
      expect.objectContaining({ id: "final", content: "recovered final" }),
    ]);
  });

  it("restores the failed Assistant after a definitive retry rejection", async () => {
    socket.retry.mockRejectedValue(Object.assign(new Error("private"), {
      code: "subtask_not_retryable",
    }));
    socket.joinTask.mockResolvedValue({
      task_id: 7,
      subtasks: [
        subtask({ id: 1, role: "USER", message_id: 5 }),
        subtask({
          id: 6,
          role: "ASSISTANT",
          message_id: 6,
          status: "FAILED",
          error_message: "provider_call_failed",
          result: { value: "preserved partial", messages_chain: [] },
        }),
      ],
      streaming: null,
    });
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));
    const failedBeforeRetry = result.current.messages[1];

    await act(async () => {
      await expect(result.current.retryAssistant(6)).rejects.toMatchObject({
        code: "subtask_not_retryable",
      });
    });
    expect(result.current.errorCode).toBe("subtask_not_retryable");
    expect(result.current.messages[1]).toEqual(failedBeforeRetry);
    expect(result.current.messages[1]).toEqual(
      expect.objectContaining({
        status: "FAILED",
        errorMessage: "provider_call_failed",
        blocks: [expect.objectContaining({ content: "preserved partial" })],
      }),
    );
  });

  it("restores immediately when retry fails before send with socket_unavailable", async () => {
    socket.retry.mockRejectedValue(Object.assign(new Error("private"), {
      code: "socket_unavailable",
    }));
    socket.joinTask.mockResolvedValue({
      task_id: 7,
      subtasks: [
        subtask({ id: 1, role: "USER", message_id: 5 }),
        subtask({
          id: 6,
          role: "ASSISTANT",
          message_id: 6,
          status: "FAILED",
          error_message: "provider_call_failed",
          result: { value: "still here", messages_chain: [] },
        }),
      ],
      streaming: null,
    });
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));
    const failedBeforeRetry = result.current.messages[1];

    await act(async () => {
      await expect(result.current.retryAssistant(6)).rejects.toMatchObject({
        code: "socket_unavailable",
      });
    });
    expect(result.current.errorCode).toBe("socket_unavailable");
    expect(result.current.messages[1]).toEqual(failedBeforeRetry);
    expect(result.current.messages[1]).toEqual(
      expect.objectContaining({
        status: "FAILED",
        errorMessage: "provider_call_failed",
        blocks: [expect.objectContaining({ content: "still here" })],
      }),
    );
  });

  it("deduplicates cancel and retry races while surfacing their stable errors", async () => {
    let rejectCancel!: (error: unknown) => void;
    socket.cancel.mockReturnValue(
      new Promise((_, reject) => {
        rejectCancel = reject;
      }),
    );
    socket.retry.mockRejectedValue(Object.assign(new Error("private"), {
      code: "subtask_not_retryable",
    }));
    const { result } = renderHook(() => useTaskChat(7));
    await waitFor(() => expect(result.current.loading).toBe(false));

    let cancelPromise!: Promise<unknown>;
    act(() => {
      cancelPromise = result.current.cancelTask();
    });
    await expect(result.current.cancelTask()).rejects.toMatchObject({
      code: "task_cancel_pending",
    });
    await act(async () => {
      rejectCancel(Object.assign(new Error("private"), { code: "task_not_running" }));
      await expect(cancelPromise).rejects.toMatchObject({ code: "task_not_running" });
    });
    expect(result.current.errorCode).toBe("task_not_running");

    await act(async () => {
      await expect(result.current.retryAssistant(9)).rejects.toMatchObject({
        code: "subtask_not_retryable",
      });
    });
    expect(result.current.errorCode).toBe("subtask_not_retryable");
  });
});
