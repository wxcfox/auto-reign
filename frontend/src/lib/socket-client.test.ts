import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SocketConnectionClient, type ConnectionSocketFactory } from "./socket-client";

type Listener = (...args: unknown[]) => void;

class FakeSocket {
  connected = false;
  disconnectCalls = 0;
  connectCalls = 0;
  listeners = new Map<string, Set<Listener>>();

  on(event: string, listener: Listener) {
    const listeners = this.listeners.get(event) ?? new Set();
    listeners.add(listener);
    this.listeners.set(event, listeners);
    return this;
  }

  off() {
    return this;
  }

  removeAllListeners() {
    this.listeners.clear();
    return this;
  }

  connect() {
    this.connectCalls += 1;
    return this;
  }

  disconnect() {
    this.disconnectCalls += 1;
    this.connected = false;
    return this;
  }

  trigger(event: string, payload?: unknown) {
    for (const listener of [...(this.listeners.get(event) ?? [])]) listener(payload);
  }

  simulateConnect() {
    this.connected = true;
    this.trigger("connect");
  }

  simulateDisconnect() {
    this.connected = false;
    this.trigger("disconnect", "transport close");
  }
}

interface ListenEvents {
  connect: () => void;
}
interface EmitEvents {
  ping: () => void;
}

function makeClient() {
  const sockets: FakeSocket[] = [];
  const factoryCalls: Array<[string, unknown]> = [];
  const socketFactory: ConnectionSocketFactory<ListenEvents, EmitEvents> = (uri, options) => {
    factoryCalls.push([uri, options]);
    const socket = new FakeSocket();
    sockets.push(socket);
    return socket as never;
  };
  const client = new SocketConnectionClient<ListenEvents, EmitEvents>({
    socketFactory,
    uri: "https://example.test/chat",
    path: "/socket.io",
  });
  return { client, sockets, factoryCalls };
}

describe("SocketConnectionClient", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("connects with the websocket-only transport and no polling fallback", () => {
    const { client, sockets, factoryCalls } = makeClient();

    client.connect("token-a");

    expect(factoryCalls).toEqual([
      [
        "https://example.test/chat",
        {
          path: "/socket.io",
          auth: { token: "token-a" },
          autoConnect: false,
          transports: ["websocket"],
        },
      ],
    ]);
    expect(sockets[0]?.connectCalls).toBe(1);
  });

  it("notifies subscribers and reconnect callbacks only after an established reconnect", () => {
    const { client, sockets } = makeClient();
    const states: boolean[] = [];
    client.subscribe((state) => states.push(state.isConnected));
    const reconnected = vi.fn();
    client.onReconnect(reconnected);

    client.connect("token-a");
    sockets[0]!.simulateConnect();
    expect(states.at(-1)).toBe(true);
    expect(reconnected).not.toHaveBeenCalled();

    sockets[0]!.simulateDisconnect();
    expect(states.at(-1)).toBe(false);

    sockets[0]!.simulateConnect();
    expect(states.at(-1)).toBe(true);
    expect(reconnected).toHaveBeenCalledTimes(1);
  });

  it("schedules a backed-off manual reconnect after a drop and reuses the same socket", () => {
    const { client, sockets } = makeClient();
    client.connect("token-a");
    sockets[0]!.simulateConnect();

    sockets[0]!.simulateDisconnect();
    expect(sockets[0]!.connectCalls).toBe(1);

    vi.advanceTimersByTime(999);
    expect(sockets[0]!.connectCalls).toBe(1);
    vi.advanceTimersByTime(1);
    expect(sockets[0]!.connectCalls).toBe(2);
    expect(sockets).toHaveLength(1);
  });

  it("stops retrying once the server rejects the identity", () => {
    const { client, sockets } = makeClient();
    client.connect("token-a");
    expect(sockets[0]!.connectCalls).toBe(1);

    sockets[0]!.trigger("connect_error", new Error("invalid_token"));

    vi.advanceTimersByTime(60_000);
    expect(sockets[0]!.connectCalls).toBe(1);
  });

  it("keeps retrying transient connect errors with growing backoff", () => {
    const { client, sockets } = makeClient();
    client.connect("token-a");
    expect(sockets[0]!.connectCalls).toBe(1);

    sockets[0]!.trigger("connect_error", new Error("xhr poll error"));
    vi.advanceTimersByTime(999);
    expect(sockets[0]!.connectCalls).toBe(1);
    vi.advanceTimersByTime(1);
    expect(sockets[0]!.connectCalls).toBe(2);

    sockets[0]!.trigger("connect_error", new Error("xhr poll error"));
    vi.advanceTimersByTime(1_999);
    expect(sockets[0]!.connectCalls).toBe(2);
    vi.advanceTimersByTime(1);
    expect(sockets[0]!.connectCalls).toBe(3);
  });

  it("ignores events from a superseded socket after a new connect", () => {
    const { client, sockets } = makeClient();
    client.subscribe(() => {});
    client.connect("token-a");
    const first = sockets[0]!;

    client.connect("token-b");
    expect(first.disconnectCalls).toBe(1);
    expect(sockets).toHaveLength(2);

    first.simulateConnect();
    expect(client.getSocket()).toBe(sockets[1]);
    expect(client.getSocket()?.connected).toBe(false);
  });

  it("does not reconnect after an explicit disconnect", () => {
    const { client, sockets } = makeClient();
    client.connect("token-a");
    sockets[0]!.simulateConnect();

    client.disconnect();
    expect(sockets[0]!.disconnectCalls).toBe(1);

    vi.advanceTimersByTime(60_000);
    expect(sockets[0]!.connectCalls).toBe(1);
    expect(client.getSocket()).toBeNull();
  });

  it("dispose tears down the connection and clears all listeners", () => {
    const { client, sockets } = makeClient();
    const stateListener = vi.fn();
    const reconnectListener = vi.fn();
    client.subscribe(stateListener);
    client.onReconnect(reconnectListener);
    client.connect("token-a");
    sockets[0]!.simulateConnect();
    stateListener.mockClear();

    client.dispose();
    stateListener.mockClear();
    sockets[0]!.trigger("connect_error", new Error("late event"));

    expect(sockets[0]!.disconnectCalls).toBe(1);
    expect(stateListener).not.toHaveBeenCalled();
  });
});
