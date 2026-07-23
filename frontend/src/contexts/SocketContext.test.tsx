import { act, render, waitFor } from "@testing-library/react";
import { useEffect } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  SocketClientError,
  SocketProvider,
  useSocket,
  type ChatSocket,
  type SocketContextValue,
  type SocketFactory,
} from "./SocketContext";
import { clearAuthToken, setAuthToken } from "@/lib/auth";

type Listener = (...args: unknown[]) => void;

class FakeSocket {
  connected = false;
  disconnectCalls = 0;
  timeoutValue: number | null = null;
  emitted: Array<{ event: string; payload: unknown }> = [];
  responses = new Map<string, Array<unknown | Promise<unknown>>>();
  syncErrors = new Map<string, Error[]>();
  listeners = new Map<string, Set<Listener>>();

  on(event: string, listener: Listener) {
    const listeners = this.listeners.get(event) ?? new Set();
    listeners.add(listener);
    this.listeners.set(event, listeners);
    return this;
  }

  off(event: string, listener?: Listener) {
    if (listener) this.listeners.get(event)?.delete(listener);
    else this.listeners.delete(event);
    return this;
  }

  removeAllListeners() {
    this.listeners.clear();
    return this;
  }

  timeout(value: number) {
    this.timeoutValue = value;
    return this;
  }

  emitWithAck(event: string, payload: unknown) {
    this.emitted.push({ event, payload });
    const errors = this.syncErrors.get(event) ?? [];
    const error = errors.shift();
    this.syncErrors.set(event, errors);
    if (error) throw error;
    const queue = this.responses.get(event) ?? [];
    const response = queue.shift();
    this.responses.set(event, queue);
    return response instanceof Promise ? response : Promise.resolve(response);
  }

  connect() {
    this.connected = true;
    this.trigger("connect");
    return this;
  }

  disconnect() {
    this.disconnectCalls += 1;
    this.connected = false;
    return this;
  }

  trigger(event: string, payload?: unknown) {
    for (const listener of this.listeners.get(event) ?? []) listener(payload);
  }

  respond(event: string, ...responses: Array<unknown | Promise<unknown>>) {
    this.responses.set(event, responses);
  }

  throwSynchronously(event: string, ...errors: Error[]) {
    this.syncErrors.set(event, errors);
  }
}

let context: SocketContextValue | null = null;

function Consumer() {
  const value = useSocket();
  useEffect(() => {
    context = value;
  }, [value]);
  return <div data-testid="connected">{String(value.connected)}</div>;
}

function renderSocket(fake: FakeSocket, ackTimeoutMs = 321) {
  const socketFactory = vi.fn(() => fake as unknown as ChatSocket) as SocketFactory;
  const view = render(
    <SocketProvider socketFactory={socketFactory} ackTimeoutMs={ackTimeoutMs}>
      <Consumer />
    </SocketProvider>,
  );
  return { ...view, socketFactory };
}

function current() {
  if (!context) throw new Error("Socket context is unavailable in the test");
  return context;
}

describe("SocketProvider", () => {
  beforeEach(() => {
    context = null;
    localStorage.clear();
    setAuthToken("private-token-value");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    localStorage.clear();
  });

  it("connects to the configured /chat namespace with path and private auth", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://api.example.test/");
    const fake = new FakeSocket();
    const { socketFactory } = renderSocket(fake);

    await waitFor(() => expect(current().connected).toBe(true));
    expect(socketFactory).toHaveBeenCalledWith("https://api.example.test/chat", {
      path: "/socket.io",
      auth: { token: "private-token-value" },
      autoConnect: false,
    });
    expect(fake.timeoutValue).toBeNull();
  });

  it("fails closed without a token and creates no socket", async () => {
    localStorage.clear();
    const fake = new FakeSocket();
    const { socketFactory } = renderSocket(fake);

    expect(socketFactory).not.toHaveBeenCalled();
    await expect(current().joinTask(7)).rejects.toMatchObject({
      code: "socket_unavailable",
    } satisfies Partial<SocketClientError>);
  });

  it("emits an incremental join and deduplicates unless force is requested", async () => {
    const fake = new FakeSocket();
    fake.respond(
      "task:join",
      { task_id: 7, subtasks: [], streaming: null },
      { task_id: 7, subtasks: [], streaming: null },
    );
    renderSocket(fake);

    const first = await current().joinTask(7, { afterMessageId: 4 });
    const duplicate = await current().joinTask(7, { afterMessageId: 9 });
    const refreshed = await current().joinTask(7, { afterMessageId: 9, force: true });

    expect(first).toEqual({ task_id: 7, subtasks: [], streaming: null });
    expect(duplicate).toBe(first);
    expect(refreshed).toEqual(first);
    expect(fake.emitted).toEqual([
      {
        event: "task:join",
        payload: { task_id: 7, after_message_id: 4 },
      },
      {
        event: "task:join",
        payload: { task_id: 7, after_message_id: 9 },
      },
    ]);
    expect(fake.timeoutValue).toBe(321);
  });

  it("removes the join cache on leave", async () => {
    const fake = new FakeSocket();
    fake.respond(
      "task:join",
      { task_id: 7, subtasks: [], streaming: null },
      { task_id: 7, subtasks: [], streaming: null },
    );
    fake.respond("task:leave", { task_id: 7 });
    renderSocket(fake);

    await current().joinTask(7);
    await current().leaveTask(7);
    await current().joinTask(7);

    expect(fake.emitted.map(({ event }) => event)).toEqual([
      "task:join",
      "task:leave",
      "task:join",
    ]);
  });

  it("registers and removes block handlers while rejecting malformed events", () => {
    const fake = new FakeSocket();
    renderSocket(fake);
    const created = vi.fn();
    const updated = vi.fn();
    const cleanup = current().registerHandlers({
      "chat:block_created": created,
      "chat:block_updated": updated,
    });
    const block = {
      id: "block-1",
      type: "text" as const,
      content: "part",
      status: "streaming" as const,
      timestamp: "2026-07-22T00:00:00Z",
    };

    fake.trigger("chat:block_created", {
      task_id: 7,
      subtask_id: 11,
      generation_id: "gen-1",
      block,
    });
    fake.trigger("chat:block_updated", { task_id: 7, block_id: "missing-envelope" });
    expect(created).toHaveBeenCalledTimes(1);
    expect(updated).not.toHaveBeenCalled();

    cleanup();
    fake.trigger("chat:block_created", {
      task_id: 7,
      subtask_id: 11,
      generation_id: "gen-1",
      block,
    });
    expect(created).toHaveBeenCalledTimes(1);
  });

  it("sends exact send/cancel/retry payloads and preserves the retry Assistant ID", async () => {
    const fake = new FakeSocket();
    fake.respond("chat:send", { task_id: 7, subtask_id: 10, message_id: 4 });
    fake.respond("chat:cancel", { task_id: 7, subtask_id: 11, accepted: true });
    fake.respond("chat:retry", { task_id: 7, subtask_id: 11 });
    renderSocket(fake);

    await expect(
      current().sendChatMessage({
        task_id: 7,
        message: "hello",
        context_ids: [3],
      }),
    ).resolves.toEqual({ task_id: 7, subtask_id: 10, message_id: 4 });
    await expect(current().cancel(7)).resolves.toEqual({
      task_id: 7,
      subtask_id: 11,
      accepted: true,
    });
    await expect(current().retry(7, 11)).resolves.toEqual({ task_id: 7, subtask_id: 11 });
    expect(fake.emitted).toEqual([
      {
        event: "chat:send",
        payload: { task_id: 7, message: "hello", context_ids: [3] },
      },
      { event: "chat:cancel", payload: { task_id: 7 } },
      { event: "chat:retry", payload: { task_id: 7, subtask_id: 11 } },
    ]);
  });

  it("preserves nullable send fields and the backend default for omitted context_ids", async () => {
    const fake = new FakeSocket();
    fake.respond("chat:send", { task_id: 8, subtask_id: 12, message_id: 1 });
    renderSocket(fake);

    await expect(
      current().sendChatMessage({
        task_id: null,
        message: "new task",
        agent_id: null,
        model_override: null,
      }),
    ).resolves.toEqual({ task_id: 8, subtask_id: 12, message_id: 1 });
    expect(fake.emitted).toEqual([
      {
        event: "chat:send",
        payload: {
          task_id: null,
          message: "new task",
          agent_id: null,
          model_override: null,
        },
      },
    ]);
  });

  it("maps server errors and malformed ACKs to stable safe codes", async () => {
    const fake = new FakeSocket();
    fake.respond(
      "chat:send",
      { error: { code: "task_running" } },
      { task_id: "private payload must not escape" },
    );
    renderSocket(fake);
    const payload = { message: "secret prompt", context_ids: [] };

    await expect(current().sendChatMessage(payload)).rejects.toMatchObject({
      code: "task_running",
      message: "Socket request failed: task_running",
    });
    await expect(current().sendChatMessage(payload)).rejects.toMatchObject({
      code: "malformed_ack",
      message: "Socket request failed: malformed_ack",
    });
  });

  it("maps cancel server errors and malformed ACKs independently", async () => {
    const fake = new FakeSocket();
    fake.respond(
      "chat:cancel",
      { error: { code: "task_not_found" } },
      { task_id: 7, subtask_id: "invalid", accepted: true },
    );
    renderSocket(fake);

    await expect(current().cancel(7)).rejects.toMatchObject({ code: "task_not_found" });
    await expect(current().cancel(7)).rejects.toMatchObject({ code: "malformed_ack" });
  });

  it("maps retry server errors and malformed or mismatched ACKs independently", async () => {
    const fake = new FakeSocket();
    fake.respond(
      "chat:retry",
      { error: { code: "subtask_not_retryable" } },
      { task_id: 7, subtask_id: 99 },
    );
    renderSocket(fake);

    await expect(current().retry(7, 11)).rejects.toMatchObject({
      code: "subtask_not_retryable",
    });
    await expect(current().retry(7, 11)).rejects.toMatchObject({ code: "malformed_ack" });
  });

  it("rejects overlong error codes and malformed active stream snapshots", async () => {
    const fake = new FakeSocket();
    fake.respond("chat:send", { error: { code: `a${"b".repeat(128)}` } });
    const snapshot = {
      task_id: 7,
      subtask_id: 11,
      generation_id: "gen-1",
      offset: 0,
      cached_content: "",
      blocks: [],
      started_at: "not-a-datetime",
      last_activity_at: "2026-07-22T00:00:00",
      status_updated: null,
    };
    fake.respond(
      "task:join",
      { task_id: 7, subtasks: [], streaming: snapshot },
      {
        task_id: 7,
        subtasks: [],
        streaming: {
          ...snapshot,
          started_at: "2026-07-22T00:00:00",
          status_updated: { status: Number.NaN },
        },
      },
    );
    renderSocket(fake);

    await expect(current().sendChatMessage({ message: "hello" })).rejects.toMatchObject({
      code: "malformed_ack",
    });
    await expect(current().joinTask(7, { force: true })).rejects.toMatchObject({
      code: "malformed_ack",
    });
    await expect(current().joinTask(7, { force: true })).rejects.toMatchObject({
      code: "malformed_ack",
    });
  });

  it("drops events with overlong generations, invalid datetimes, or unsafe status JSON", () => {
    const fake = new FakeSocket();
    renderSocket(fake);
    const started = vi.fn();
    const taskCreated = vi.fn();
    const statusUpdated = vi.fn();
    current().registerHandlers({
      "chat:start": started,
      "task:created": taskCreated,
      "chat:status_updated": statusUpdated,
    });

    fake.trigger("chat:start", {
      task_id: 7,
      subtask_id: 11,
      generation_id: "g".repeat(65),
      status: "RUNNING",
    });
    fake.trigger("task:created", {
      task: {
        id: 7,
        name: "Task",
        href: "/chat?task=7",
        status: "RUNNING",
        agent: { id: null, name: "No agent", is_available: true },
        model_override: null,
        created_at: "2026-02-30T00:00:00",
        updated_at: "2026-07-22T00:00:00",
      },
    });
    fake.trigger("chat:status_updated", {
      task_id: 7,
      subtask_id: 11,
      generation_id: "gen-1",
      status: { progress: Number.POSITIVE_INFINITY },
    });

    expect(started).not.toHaveBeenCalled();
    expect(taskCreated).not.toHaveBeenCalled();
    expect(statusUpdated).not.toHaveBeenCalled();
  });

  it("reports reconnect state and clears stale join acknowledgements", async () => {
    const fake = new FakeSocket();
    fake.respond(
      "task:join",
      { task_id: 7, subtasks: [], streaming: null },
      { task_id: 7, subtasks: [], streaming: null },
    );
    renderSocket(fake);
    await current().joinTask(7);
    const reconnected = vi.fn();
    const cleanup = current().onReconnect(reconnected);

    act(() => fake.trigger("disconnect"));
    expect(current().connected).toBe(false);
    act(() => {
      fake.connected = true;
      fake.trigger("connect");
    });
    expect(reconnected).toHaveBeenCalledTimes(1);
    await current().joinTask(7, { afterMessageId: 4, force: true });
    expect(fake.emitted.filter(({ event }) => event === "task:join")).toHaveLength(2);
    cleanup();
  });

  it("rejects timed out acknowledgements with a stable code", async () => {
    const fake = new FakeSocket();
    fake.respond("chat:send", Promise.reject(new Error("operation has timed out")));
    renderSocket(fake);

    await expect(
      current().sendChatMessage({ message: "hello", context_ids: [] }),
    ).rejects.toMatchObject({ code: "socket_timeout" });
  });

  it("normalizes synchronous transport throws and cleans the pending request", async () => {
    const fake = new FakeSocket();
    fake.throwSynchronously("chat:send", new Error("secret transport detail"));
    renderSocket(fake);

    const failed = current().sendChatMessage({ message: "first" });
    await expect(failed).rejects.toMatchObject({
      code: "socket_disconnected",
      message: "Socket request failed: socket_disconnected",
    });

    const forever = new Promise<unknown>(() => undefined);
    fake.respond("chat:send", forever);
    const pending = current().sendChatMessage({ message: "second" });
    act(() => fake.trigger("disconnect"));
    await expect(pending).rejects.toMatchObject({ code: "socket_disconnected" });
  });

  it("disconnects immediately on logout and rejects the old identity's pending ACK", async () => {
    const fake = new FakeSocket();
    fake.respond("chat:send", new Promise<unknown>(() => undefined));
    const { socketFactory } = renderSocket(fake);
    const pending = current().sendChatMessage({ message: "private prompt" });

    act(() => clearAuthToken());

    await expect(pending).rejects.toMatchObject({ code: "socket_disconnected" });
    await waitFor(() => expect(fake.disconnectCalls).toBe(1));
    expect(socketFactory).toHaveBeenCalledTimes(1);
    expect(current().connected).toBe(false);
  });

  it("tears down the old socket and reconnects with a rotated token", async () => {
    const first = new FakeSocket();
    const second = new FakeSocket();
    first.respond("chat:send", new Promise<unknown>(() => undefined));
    const socketFactoryMock = vi
      .fn()
      .mockReturnValueOnce(first as unknown as ChatSocket)
      .mockReturnValueOnce(second as unknown as ChatSocket);
    const socketFactory = socketFactoryMock as SocketFactory;
    render(
      <SocketProvider socketFactory={socketFactory} ackTimeoutMs={321}>
        <Consumer />
      </SocketProvider>,
    );
    await waitFor(() => expect(current().connected).toBe(true));
    const pending = current().sendChatMessage({ message: "old identity prompt" });

    act(() => setAuthToken("rotated-private-token"));

    await expect(pending).rejects.toMatchObject({ code: "socket_disconnected" });
    await waitFor(() => expect(socketFactory).toHaveBeenCalledTimes(2));
    expect(first.disconnectCalls).toBe(1);
    expect(socketFactoryMock.mock.calls[1]).toEqual([
      "/chat",
      {
        path: "/socket.io",
        auth: { token: "rotated-private-token" },
        autoConnect: false,
      },
    ]);
    expect(second.connected).toBe(true);
  });

  it("rejects pending acknowledgements on disconnect and provider unmount", async () => {
    const fake = new FakeSocket();
    const forever = new Promise<unknown>(() => undefined);
    fake.respond("chat:send", forever, forever);
    const view = renderSocket(fake);

    const disconnected = current().sendChatMessage({ message: "first", context_ids: [] });
    act(() => fake.trigger("disconnect"));
    await expect(disconnected).rejects.toMatchObject({ code: "socket_disconnected" });

    act(() => {
      fake.connected = true;
      fake.trigger("connect");
    });
    const unmounted = current().sendChatMessage({ message: "second", context_ids: [] });
    view.unmount();
    await expect(unmounted).rejects.toMatchObject({ code: "socket_unmounted" });
    expect(fake.disconnectCalls).toBe(1);
    expect(fake.listeners.size).toBe(0);
  });
});
