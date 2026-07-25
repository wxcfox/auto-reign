"use client";

import type { ManagerOptions, Socket, SocketOptions } from "socket.io-client";

/** Engine.IO transport allow-list: WebSocket only, no HTTP long-polling fallback. */
const TRANSPORTS = ["websocket"];

export type ConnectionSocketFactory<
  ListenEvents extends object,
  EmitEvents extends object,
> = (
  uri: string,
  options: Partial<ManagerOptions & SocketOptions>,
) => Socket<ListenEvents, EmitEvents>;

export interface SocketConnectionState<
  ListenEvents extends object,
  EmitEvents extends object,
> {
  socket: Socket<ListenEvents, EmitEvents> | null;
  isConnected: boolean;
}

export type SocketConnectionStateListener<
  ListenEvents extends object,
  EmitEvents extends object,
> = (state: SocketConnectionState<ListenEvents, EmitEvents>) => void;

export interface SocketConnectionOptions<
  ListenEvents extends object,
  EmitEvents extends object,
> {
  socketFactory: ConnectionSocketFactory<ListenEvents, EmitEvents>;
  uri: string;
  path: string;
  /** Backoff before a manual reconnect attempt, keyed by consecutive attempt count (1-based). */
  reconnectDelayMs?: (attempt: number) => number;
  /** Distinguishes a rejected identity (stop retrying) from a transient drop (keep retrying). */
  isAuthError?: (error: Error) => boolean;
}

function defaultReconnectDelayMs(attempt: number): number {
  return Math.min(1000 * 2 ** Math.min(attempt - 1, 3), 5_000);
}

function defaultIsAuthError(error: Error): boolean {
  const message = error.message?.toLowerCase() ?? "";
  return (
    message.includes("invalid_token") ||
    message.includes("auth_required") ||
    message.includes("auth_unavailable")
  );
}

/**
 * Owns a single Socket.IO connection restricted to the WebSocket transport
 * (no long-polling handshake or fallback) and drives its own reconnect
 * schedule so a rejected identity can stop retrying instead of hammering the
 * server with the same invalid token.
 *
 * A monotonically increasing generation counter guards every socket
 * callback: once `connect`/`disconnect` moves to a new generation, events
 * from a superseded socket are ignored instead of racing app state.
 */
export class SocketConnectionClient<
  ListenEvents extends object,
  EmitEvents extends object,
> {
  private readonly socketFactory: ConnectionSocketFactory<ListenEvents, EmitEvents>;
  private readonly uri: string;
  private readonly path: string;
  private readonly reconnectDelayMs: (attempt: number) => number;
  private readonly isAuthError: (error: Error) => boolean;

  private socket: Socket<ListenEvents, EmitEvents> | null = null;
  private isConnected = false;
  private generation = 0;
  private authToken: string | null = null;
  private intentionalDisconnect = true;
  private hasConnectedOnce = false;
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  private readonly stateListeners = new Set<
    SocketConnectionStateListener<ListenEvents, EmitEvents>
  >();
  private readonly reconnectCallbacks = new Set<() => void>();

  constructor(options: SocketConnectionOptions<ListenEvents, EmitEvents>) {
    this.socketFactory = options.socketFactory;
    this.uri = options.uri;
    this.path = options.path;
    this.reconnectDelayMs = options.reconnectDelayMs ?? defaultReconnectDelayMs;
    this.isAuthError = options.isAuthError ?? defaultIsAuthError;
  }

  getSocket(): Socket<ListenEvents, EmitEvents> | null {
    return this.socket;
  }

  subscribe(
    listener: SocketConnectionStateListener<ListenEvents, EmitEvents>,
  ): () => void {
    this.stateListeners.add(listener);
    return () => {
      this.stateListeners.delete(listener);
    };
  }

  onReconnect(callback: () => void): () => void {
    this.reconnectCallbacks.add(callback);
    return () => {
      this.reconnectCallbacks.delete(callback);
    };
  }

  connect(token: string): void {
    this.authToken = token;
    this.intentionalDisconnect = false;
    this.hasConnectedOnce = false;
    this.reconnectAttempt = 0;
    this.generation += 1;
    const generation = this.generation;
    this.clearReconnectTimer();
    this.teardownSocket();

    const socket = this.socketFactory(this.uri, {
      path: this.path,
      auth: { token },
      autoConnect: false,
      transports: TRANSPORTS,
      reconnection: false,
    });
    this.bindSocketHandlers(socket, generation);
    this.socket = socket;
    this.notifyState();
    socket.connect();
  }

  disconnect(): void {
    this.authToken = null;
    this.intentionalDisconnect = true;
    this.generation += 1;
    this.clearReconnectTimer();
    this.reconnectAttempt = 0;
    this.hasConnectedOnce = false;
    this.teardownSocket();
    this.notifyState();
  }

  dispose(): void {
    this.disconnect();
    this.stateListeners.clear();
    this.reconnectCallbacks.clear();
  }

  private bindSocketHandlers(
    socket: Socket<ListenEvents, EmitEvents>,
    generation: number,
  ): void {
    socket.on("connect", () => {
      if (generation !== this.generation) return;
      this.clearReconnectTimer();
      this.reconnectAttempt = 0;
      this.isConnected = true;
      const shouldNotifyReconnect = this.hasConnectedOnce;
      this.hasConnectedOnce = true;
      this.notifyState();
      if (shouldNotifyReconnect) this.notifyReconnect();
    });

    socket.on("disconnect", (reason: string) => {
      if (generation !== this.generation) return;
      this.isConnected = false;
      this.notifyState();
      if (reason === "io server disconnect" || reason === "io client disconnect") {
        return;
      }
      this.scheduleReconnect(generation);
    });

    socket.on("connect_error", (error: Error) => {
      if (generation !== this.generation) return;
      this.isConnected = false;
      this.notifyState();
      if (this.isAuthError(error)) {
        this.intentionalDisconnect = true;
        this.clearReconnectTimer();
        return;
      }
      this.scheduleReconnect(generation);
    });
  }

  private scheduleReconnect(generation: number): void {
    if (
      this.intentionalDisconnect ||
      this.reconnectTimer !== null ||
      !this.authToken
    ) {
      return;
    }
    this.reconnectAttempt += 1;
    const delay = this.reconnectDelayMs(this.reconnectAttempt);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (generation !== this.generation || this.intentionalDisconnect) return;
      this.socket?.connect();
    }, delay);
  }

  private teardownSocket(): void {
    const socket = this.socket;
    this.socket = null;
    this.isConnected = false;
    if (!socket) return;
    socket.removeAllListeners();
    socket.disconnect();
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer === null) return;
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private notifyState(): void {
    const state: SocketConnectionState<ListenEvents, EmitEvents> = {
      socket: this.socket,
      isConnected: this.isConnected,
    };
    for (const listener of this.stateListeners) listener(state);
  }

  private notifyReconnect(): void {
    for (const callback of this.reconnectCallbacks) callback();
  }
}
