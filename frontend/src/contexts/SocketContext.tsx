"use client";

import { io, type ManagerOptions, type Socket, type SocketOptions } from "socket.io-client";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { getAuthToken, subscribeAuthToken } from "@/lib/auth";
import type {
  ChatCancelAck,
  ChatRetryAck,
  ChatSendAck,
  ChatSendPayload,
  ClientToServerEvents,
  ServerToClientEvents,
  SocketErrorAck,
  SocketEventHandlers,
  SocketEventName,
  TaskJoinAck,
  TaskLeaveAck,
} from "@/lib/socket-types";

const DEFAULT_ACK_TIMEOUT_MS = 10_000;
const MAX_GENERATION_ID_LENGTH = 64;
const MAX_SOCKET_ERROR_CODE_LENGTH = 128;
const SERVER_EVENT_NAMES: readonly SocketEventName[] = [
  "chat:start",
  "chat:chunk",
  "chat:block_created",
  "chat:block_updated",
  "chat:done",
  "chat:error",
  "chat:cancelled",
  "chat:status_updated",
  "task:created",
  "task:status",
];

export type ChatSocket = Socket<ServerToClientEvents, ClientToServerEvents>;
export type SocketFactory = (
  uri: string,
  options: Partial<ManagerOptions & SocketOptions>,
) => ChatSocket;

export class SocketClientError extends Error {
  constructor(public readonly code: string) {
    super(`Socket request failed: ${code}`);
    this.name = "SocketClientError";
  }
}

export interface JoinTaskOptions {
  afterMessageId?: number | null;
  force?: boolean;
}

export interface SocketContextValue {
  connected: boolean;
  joinTask: (taskId: number, options?: JoinTaskOptions) => Promise<TaskJoinAck>;
  leaveTask: (taskId: number) => Promise<TaskLeaveAck>;
  sendChatMessage: (payload: ChatSendPayload) => Promise<ChatSendAck>;
  cancel: (taskId: number) => Promise<ChatCancelAck>;
  retry: (taskId: number, assistantSubtaskId: number) => Promise<ChatRetryAck>;
  registerHandlers: (handlers: SocketEventHandlers) => () => void;
  onReconnect: (callback: () => void) => () => void;
}

const SocketContext = createContext<SocketContextValue | null>(null);

export interface SocketProviderProps {
  children: ReactNode;
  socketFactory?: SocketFactory;
  ackTimeoutMs?: number;
}

interface HandlerRegistration {
  handlers: SocketEventHandlers;
  socket: ChatSocket | null;
  listeners: Array<[SocketEventName, (payload: unknown) => void]>;
}

export function SocketProvider({
  children,
  socketFactory = io,
  ackTimeoutMs = DEFAULT_ACK_TIMEOUT_MS,
}: SocketProviderProps) {
  const [authToken, setAuthTokenSnapshot] = useState<string | null>(() => getAuthToken());
  const socketRef = useRef<ChatSocket | null>(null);
  const joinedTasksRef = useRef(new Map<number, Promise<TaskJoinAck>>());
  const pendingRejectorsRef = useRef(new Set<(error: SocketClientError) => void>());
  const reconnectCallbacksRef = useRef(new Set<() => void>());
  const handlerRegistrationsRef = useRef(new Set<HandlerRegistration>());
  const hasConnectedRef = useRef(false);
  const reconnectPendingRef = useRef(false);
  const unmountedRef = useRef(false);
  const [connected, setConnected] = useState(false);

  const rejectPending = useCallback((code: string) => {
    const error = new SocketClientError(code);
    const rejectors = [...pendingRejectorsRef.current];
    pendingRejectorsRef.current.clear();
    for (const reject of rejectors) {
      reject(error);
    }
  }, []);

  useEffect(() => {
    const syncToken = () => setAuthTokenSnapshot(getAuthToken());
    const unsubscribe = subscribeAuthToken(syncToken);
    syncToken();
    return unsubscribe;
  }, []);

  useEffect(() => {
    unmountedRef.current = false;
    return () => {
      unmountedRef.current = true;
      rejectPending("socket_unmounted");
    };
  }, [rejectPending]);

  useEffect(() => {
    const joinedTasks = joinedTasksRef.current;
    const handlerRegistrations = handlerRegistrationsRef.current;
    if (!authToken) {
      socketRef.current = null;
      setConnected(false);
      return () => {
        joinedTasks.clear();
        rejectPending(
          unmountedRef.current ? "socket_unmounted" : "socket_disconnected",
        );
      };
    }

    const socket = socketFactory(socketNamespaceUrl(), {
      path: "/socket.io",
      auth: { token: authToken },
      autoConnect: false,
    });
    socketRef.current = socket;

    const handleConnect = () => {
      setConnected(true);
      if (hasConnectedRef.current && reconnectPendingRef.current) {
        reconnectPendingRef.current = false;
        for (const callback of reconnectCallbacksRef.current) {
          callback();
        }
      }
      hasConnectedRef.current = true;
    };
    const handleDisconnect = () => {
      setConnected(false);
      joinedTasks.clear();
      reconnectPendingRef.current = hasConnectedRef.current;
      rejectPending("socket_disconnected");
    };
    const handleConnectError = () => {
      setConnected(false);
      rejectPending("socket_disconnected");
    };

    socket.on("connect", handleConnect);
    socket.on("disconnect", handleDisconnect);
    socket.on("connect_error", handleConnectError);
    for (const registration of handlerRegistrations) {
      attachHandlerRegistration(registration, socket);
    }
    socket.connect();

    return () => {
      joinedTasks.clear();
      rejectPending(
        unmountedRef.current ? "socket_unmounted" : "socket_disconnected",
      );
      socket.off("connect", handleConnect);
      socket.off("disconnect", handleDisconnect);
      socket.off("connect_error", handleConnectError);
      for (const registration of handlerRegistrations) {
        detachHandlerRegistration(registration);
      }
      socket.removeAllListeners();
      socket.disconnect();
      if (socketRef.current === socket) {
        socketRef.current = null;
      }
      hasConnectedRef.current = false;
      reconnectPendingRef.current = false;
      setConnected(false);
    };
  }, [authToken, rejectPending, socketFactory]);

  const requireSocket = useCallback(() => {
    const socket = socketRef.current;
    if (!socket) {
      throw new SocketClientError("socket_unavailable");
    }
    if (!socket.connected) {
      throw new SocketClientError("socket_disconnected");
    }
    return socket;
  }, []);

  const awaitAck = useCallback(
    <T,>(request: () => Promise<unknown>, validator: (value: unknown) => value is T) => {
      if (unmountedRef.current) {
        return Promise.reject<T>(new SocketClientError("socket_unmounted"));
      }
      return new Promise<T>((resolve, reject) => {
        let settled = false;
        const cleanup = () => {
          pendingRejectorsRef.current.delete(rejectPendingRequest);
        };
        const rejectWith = (error: SocketClientError) => {
          if (settled) return;
          settled = true;
          cleanup();
          reject(error);
        };
        const resolveWith = (value: T) => {
          if (settled) return;
          settled = true;
          cleanup();
          resolve(value);
        };
        const rejectPendingRequest = (error: SocketClientError) => {
          rejectWith(error);
        };
        pendingRejectorsRef.current.add(rejectPendingRequest);
        let operation: Promise<unknown>;
        try {
          operation = Promise.resolve(request());
        } catch {
          rejectWith(new SocketClientError("socket_disconnected"));
          return;
        }
        void operation.then(
          (response) => {
            if (settled) return;
            try {
              if (isErrorAck(response)) {
                rejectWith(new SocketClientError(response.error.code));
              } else if (!validator(response)) {
                rejectWith(new SocketClientError("malformed_ack"));
              } else {
                resolveWith(response);
              }
            } catch {
              rejectWith(new SocketClientError("malformed_ack"));
            }
          },
          (error: unknown) => {
            rejectWith(
              new SocketClientError(
                isTimeoutError(error) ? "socket_timeout" : "socket_disconnected",
              ),
            );
          },
        );
      });
    },
    [],
  );

  const joinTask = useCallback(
    async (taskId: number, options: JoinTaskOptions = {}) => {
      if (
        !isPositiveInteger(taskId) ||
        (options.afterMessageId !== undefined &&
          options.afterMessageId !== null &&
          !isNonNegativeInteger(options.afterMessageId))
      ) {
        throw new SocketClientError("invalid_payload");
      }
      if (!options.force) {
        const cached = joinedTasksRef.current.get(taskId);
        if (cached) return await cached;
      }
      const socket = requireSocket();
      const request = awaitAck(
        () =>
          socket.timeout(ackTimeoutMs).emitWithAck("task:join", {
            task_id: taskId,
            after_message_id: options.afterMessageId ?? null,
          }),
        (value): value is TaskJoinAck =>
          isTaskJoinAck(value) && value.task_id === taskId,
      );
      joinedTasksRef.current.set(taskId, request);
      void request.catch(() => {
        if (joinedTasksRef.current.get(taskId) === request) {
          joinedTasksRef.current.delete(taskId);
        }
      });
      return await request;
    },
    [ackTimeoutMs, awaitAck, requireSocket],
  );

  const leaveTask = useCallback(
    async (taskId: number) => {
      if (!isPositiveInteger(taskId)) {
        throw new SocketClientError("invalid_payload");
      }
      joinedTasksRef.current.delete(taskId);
      const socket = requireSocket();
      return await awaitAck(
        () => socket.timeout(ackTimeoutMs).emitWithAck("task:leave", { task_id: taskId }),
        (value): value is TaskLeaveAck =>
          isTaskLeaveAck(value) && value.task_id === taskId,
      );
    },
    [ackTimeoutMs, awaitAck, requireSocket],
  );

  const sendChatMessage = useCallback(
    async (payload: ChatSendPayload) => {
      if (!isChatSendRequestPayload(payload)) {
        throw new SocketClientError("invalid_payload");
      }
      const socket = requireSocket();
      return await awaitAck(
        () => socket.timeout(ackTimeoutMs).emitWithAck("chat:send", payload),
        (value): value is ChatSendAck =>
          isChatSendAck(value) &&
          (payload.task_id === undefined ||
            payload.task_id === null ||
            value.task_id === payload.task_id),
      );
    },
    [ackTimeoutMs, awaitAck, requireSocket],
  );

  const cancel = useCallback(
    async (taskId: number) => {
      if (!isPositiveInteger(taskId)) {
        throw new SocketClientError("invalid_payload");
      }
      const socket = requireSocket();
      return await awaitAck(
        () => socket.timeout(ackTimeoutMs).emitWithAck("chat:cancel", { task_id: taskId }),
        (value): value is ChatCancelAck =>
          isChatCancelAck(value) && value.task_id === taskId,
      );
    },
    [ackTimeoutMs, awaitAck, requireSocket],
  );

  const retry = useCallback(
    async (taskId: number, assistantSubtaskId: number) => {
      if (!isPositiveInteger(taskId) || !isPositiveInteger(assistantSubtaskId)) {
        throw new SocketClientError("invalid_payload");
      }
      const socket = requireSocket();
      return await awaitAck(
        () =>
          socket.timeout(ackTimeoutMs).emitWithAck("chat:retry", {
            task_id: taskId,
            subtask_id: assistantSubtaskId,
          }),
        (value): value is ChatRetryAck =>
          isChatRetryAck(value) &&
          value.task_id === taskId &&
          value.subtask_id === assistantSubtaskId,
      );
    },
    [ackTimeoutMs, awaitAck, requireSocket],
  );

  const registerHandlers = useCallback((handlers: SocketEventHandlers) => {
    const registration: HandlerRegistration = {
      handlers,
      socket: null,
      listeners: [],
    };
    handlerRegistrationsRef.current.add(registration);
    const socket = socketRef.current;
    if (socket) attachHandlerRegistration(registration, socket);
    return () => {
      handlerRegistrationsRef.current.delete(registration);
      detachHandlerRegistration(registration);
    };
  }, []);

  const onReconnect = useCallback((callback: () => void) => {
    reconnectCallbacksRef.current.add(callback);
    return () => reconnectCallbacksRef.current.delete(callback);
  }, []);

  const value: SocketContextValue = {
    connected,
    joinTask,
    leaveTask,
    sendChatMessage,
    cancel,
    retry,
    registerHandlers,
    onReconnect,
  };

  return <SocketContext.Provider value={value}>{children}</SocketContext.Provider>;
}

export function useSocket(): SocketContextValue {
  const value = useContext(SocketContext);
  if (!value) {
    throw new Error("useSocket must be used within SocketProvider");
  }
  return value;
}

function socketNamespaceUrl() {
  const base = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").replace(/\/$/, "");
  return `${base}/chat`;
}

function attachHandlerRegistration(
  registration: HandlerRegistration,
  socket: ChatSocket,
) {
  detachHandlerRegistration(registration);
  registration.socket = socket;
  for (const eventName of SERVER_EVENT_NAMES) {
    const handler = registration.handlers[eventName] as
      | ((payload: never) => void)
      | undefined;
    if (!handler) continue;
    const wrapped = (payload: unknown) => {
      if (isServerEventPayload(eventName, payload)) {
        handler(payload as never);
      }
    };
    registration.listeners.push([eventName, wrapped]);
    socket.on(eventName as never, wrapped as never);
  }
}

function detachHandlerRegistration(registration: HandlerRegistration) {
  if (registration.socket) {
    for (const [eventName, listener] of registration.listeners) {
      registration.socket.off(eventName as never, listener as never);
    }
  }
  registration.socket = null;
  registration.listeners = [];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isChatSendRequestPayload(value: unknown): value is ChatSendPayload {
  if (!isRecord(value)) return false;
  const allowed = new Set([
    "task_id",
    "message",
    "agent_id",
    "model_override",
    "context_ids",
  ]);
  if (!Object.keys(value).every((key) => allowed.has(key))) return false;
  if (
    value.task_id !== undefined &&
    value.task_id !== null &&
    !isPositiveInteger(value.task_id)
  ) {
    return false;
  }
  if (
    typeof value.message !== "string" ||
    unicodeLength(value.message) < 1 ||
    unicodeLength(value.message) > 20_000
  ) {
    return false;
  }
  if (
    value.agent_id !== undefined &&
    value.agent_id !== null &&
    (typeof value.agent_id !== "string" ||
      unicodeLength(value.agent_id) < 1 ||
      unicodeLength(value.agent_id) > 36)
  ) {
    return false;
  }
  if (
    value.model_override !== undefined &&
    value.model_override !== null &&
    !isModelRef(value.model_override)
  ) {
    return false;
  }
  if (value.context_ids === undefined) return true;
  return (
    Array.isArray(value.context_ids) &&
    value.context_ids.length <= 10 &&
    value.context_ids.every(isPositiveInteger) &&
    new Set(value.context_ids).size === value.context_ids.length
  );
}

function isErrorAck(value: unknown): value is SocketErrorAck {
  return (
    isRecord(value) &&
    Object.keys(value).length === 1 &&
    isRecord(value.error) &&
    Object.keys(value.error).length === 1 &&
    typeof value.error.code === "string" &&
    unicodeLength(value.error.code) <= MAX_SOCKET_ERROR_CODE_LENGTH &&
    /^[a-z][a-z0-9_]*$/.test(value.error.code)
  );
}

function isTaskJoinAck(value: unknown): value is TaskJoinAck {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["task_id", "subtasks", "streaming"]) &&
    isPositiveInteger(value.task_id) &&
    Array.isArray(value.subtasks) &&
    value.subtasks.every(isSubtask) &&
    (value.streaming === null || isActiveStreamSnapshot(value.streaming))
  );
}

function isTaskLeaveAck(value: unknown): value is TaskLeaveAck {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["task_id"]) &&
    isPositiveInteger(value.task_id)
  );
}

function isChatSendAck(value: unknown): value is ChatSendAck {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["task_id", "subtask_id", "message_id"]) &&
    isPositiveInteger(value.task_id) &&
    isPositiveInteger(value.subtask_id) &&
    isPositiveInteger(value.message_id)
  );
}

function isChatCancelAck(value: unknown): value is ChatCancelAck {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["task_id", "subtask_id", "accepted"]) &&
    isPositiveInteger(value.task_id) &&
    (value.subtask_id === null || isPositiveInteger(value.subtask_id)) &&
    typeof value.accepted === "boolean"
  );
}

function isChatRetryAck(value: unknown): value is ChatRetryAck {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["task_id", "subtask_id"]) &&
    isPositiveInteger(value.task_id) &&
    isPositiveInteger(value.subtask_id)
  );
}

function isSubtask(value: unknown) {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      "id",
      "task_id",
      "role",
      "message_id",
      "parent_id",
      "prompt",
      "status",
      "progress",
      "result",
      "error_message",
      "contexts",
      "created_at",
      "updated_at",
      "completed_at",
    ]) &&
    isPositiveInteger(value.id) &&
    isPositiveInteger(value.task_id) &&
    (value.role === "USER" || value.role === "ASSISTANT") &&
    isPositiveInteger(value.message_id) &&
    (value.parent_id === null || isPositiveInteger(value.parent_id)) &&
    typeof value.prompt === "string" &&
    isTaskStatus(value.status) &&
    isNonNegativeInteger(value.progress) &&
    (value.result === null || isAssistantResult(value.result)) &&
    (value.error_message === null || typeof value.error_message === "string") &&
    Array.isArray(value.contexts) &&
    value.contexts.every(isContext) &&
    typeof value.created_at === "string" &&
    isIsoDateTime(value.created_at) &&
    typeof value.updated_at === "string" &&
    isIsoDateTime(value.updated_at) &&
    (value.completed_at === null ||
      (typeof value.completed_at === "string" && isIsoDateTime(value.completed_at)))
  );
}

function isContext(value: unknown) {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      "id",
      "context_type",
      "name",
      "status",
      "mime_type",
      "file_extension",
      "file_size",
      "text_length",
      "type_data",
    ]) &&
    isPositiveInteger(value.id) &&
    ["attachment", "knowledge_base", "selected_documents"].includes(
      String(value.context_type),
    ) &&
    typeof value.name === "string" &&
    ["pending", "uploading", "parsing", "ready", "empty", "failed"].includes(
      String(value.status),
    ) &&
    (value.mime_type === null || typeof value.mime_type === "string") &&
    (value.file_extension === null || typeof value.file_extension === "string") &&
    (value.file_size === null || isNonNegativeInteger(value.file_size)) &&
    isNonNegativeInteger(value.text_length) &&
    isJsonObject(value.type_data)
  );
}

function isActiveStreamSnapshot(value: unknown) {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      "task_id",
      "subtask_id",
      "generation_id",
      "offset",
      "cached_content",
      "blocks",
      "started_at",
      "last_activity_at",
      "status_updated",
    ]) &&
    isPositiveInteger(value.task_id) &&
    isPositiveInteger(value.subtask_id) &&
    typeof value.generation_id === "string" &&
    value.generation_id.length > 0 &&
    unicodeLength(value.generation_id) <= MAX_GENERATION_ID_LENGTH &&
    isNonNegativeInteger(value.offset) &&
    typeof value.cached_content === "string" &&
    Array.isArray(value.blocks) &&
    value.blocks.every(isChatBlock) &&
    isIsoDateTime(value.started_at) &&
    isIsoDateTime(value.last_activity_at) &&
    (value.status_updated === null || isJsonObject(value.status_updated))
  );
}

function isChatBlock(value: unknown) {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    !isCanonicalUtcTimestamp(value.timestamp)
  ) {
    return false;
  }
  if (value.type === "text") {
    return (
      hasExactKeys(value, ["id", "type", "content", "status", "timestamp"]) &&
      isBlockId(value.id) &&
      typeof value.content === "string" &&
      (value.status === "streaming" || value.status === "done")
    );
  }
  const expectedKeys =
    "tool_output" in value
      ? ["id", "type", "tool_use_id", "tool_name", "tool_input", "tool_output", "status", "timestamp"]
      : ["id", "type", "tool_use_id", "tool_name", "tool_input", "status", "timestamp"];
  return (
    value.type === "tool" &&
    hasExactKeys(value, expectedKeys) &&
    isBlockId(value.id) &&
    typeof value.tool_use_id === "string" &&
    value.tool_use_id.length > 0 &&
    typeof value.tool_name === "string" &&
    value.tool_name.length > 0 &&
    isJsonObject(value.tool_input) &&
    (!("tool_output" in value) || isJsonValue(value.tool_output)) &&
    ["generating_arguments", "pending", "done", "error"].includes(String(value.status)) &&
    (["done", "error"].includes(String(value.status)) === ("tool_output" in value))
  );
}

function isTaskStatus(value: unknown) {
  return ["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"].includes(
    String(value),
  );
}

function isGenerationEvent(value: unknown): value is Record<string, unknown> {
  return (
    isRecord(value) &&
    isPositiveInteger(value.task_id) &&
    isPositiveInteger(value.subtask_id) &&
    typeof value.generation_id === "string" &&
    value.generation_id.length > 0 &&
    unicodeLength(value.generation_id) <= MAX_GENERATION_ID_LENGTH
  );
}

function isTaskBrief(value: unknown) {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      "id",
      "name",
      "href",
      "status",
      "agent",
      "model_override",
      "created_at",
      "updated_at",
    ]) &&
    isPositiveInteger(value.id) &&
    typeof value.name === "string" &&
    typeof value.href === "string" &&
    isTaskStatus(value.status) &&
    isRecord(value.agent) &&
    hasExactKeys(value.agent, ["id", "name", "is_available"]) &&
    (value.agent.id === null || typeof value.agent.id === "string") &&
    typeof value.agent.name === "string" &&
    typeof value.agent.is_available === "boolean" &&
    (value.model_override === null || isModelRef(value.model_override)) &&
    isIsoDateTime(value.created_at) &&
    isIsoDateTime(value.updated_at)
  );
}

function isServerEventPayload(eventName: SocketEventName, value: unknown): boolean {
  if (eventName === "task:created" || eventName === "task:status") {
    return (
      isRecord(value) &&
      hasExactKeys(value, ["task"]) &&
      isTaskBrief(value.task)
    );
  }
  if (!isGenerationEvent(value)) return false;
  if (eventName === "chat:start") {
    return (
      hasExactKeys(value, ["task_id", "subtask_id", "generation_id", "status"]) &&
      value.status === "RUNNING"
    );
  }
  if (eventName === "chat:chunk") {
    return (
      hasExactKeys(value, [
        "task_id",
        "subtask_id",
        "generation_id",
        "block_id",
        "block_offset",
        "offset",
        "content",
      ]) &&
      isBlockId(value.block_id) &&
      isNonNegativeInteger(value.block_offset) &&
      isNonNegativeInteger(value.offset) &&
      typeof value.content === "string" &&
      value.content.length > 0
    );
  }
  if (eventName === "chat:block_created") {
    return (
      hasExactKeys(value, ["task_id", "subtask_id", "generation_id", "block"]) &&
      isChatBlock(value.block)
    );
  }
  if (eventName === "chat:block_updated") {
    return (
      hasExactKeys(value, [
        "task_id",
        "subtask_id",
        "generation_id",
        "block_id",
        "content",
        "tool_input",
        "tool_output",
        "status",
      ]) &&
      isBlockId(value.block_id) &&
      (value.content === null || typeof value.content === "string") &&
      (value.tool_input === null || isJsonObject(value.tool_input)) &&
      isJsonValue(value.tool_output) &&
      (value.status === null ||
        ["generating_arguments", "pending", "streaming", "done", "error"].includes(
          String(value.status),
        ))
    );
  }
  if (eventName === "chat:done") {
    return (
      hasExactKeys(value, ["task_id", "subtask_id", "generation_id", "result"]) &&
      isAssistantCompletedResult(value.result)
    );
  }
  if (eventName === "chat:error") {
    return (
      hasExactKeys(value, ["task_id", "subtask_id", "generation_id", "code", "result"]) &&
      typeof value.code === "string" &&
      value.code.length > 0 &&
      unicodeLength(value.code) <= MAX_SOCKET_ERROR_CODE_LENGTH &&
      (value.result === null || isAssistantPartialResult(value.result))
    );
  }
  if (eventName === "chat:cancelled") {
    return (
      hasExactKeys(value, ["task_id", "subtask_id", "generation_id", "result"]) &&
      (value.result === null || isAssistantPartialResult(value.result))
    );
  }
  return (
    eventName === "chat:status_updated" &&
    hasExactKeys(value, ["task_id", "subtask_id", "generation_id", "status"]) &&
    isJsonObject(value.status)
  );
}

function isTimeoutError(error: unknown) {
  return error instanceof Error && /tim(?:eout|ed out)/i.test(error.message);
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]) {
  const keys = Object.keys(value);
  return keys.length === expected.length && expected.every((key) => key in value);
}

function isBlockId(value: unknown) {
  return (
    typeof value === "string" &&
    /^[A-Za-z0-9._:-]{1,36}$/.test(value)
  );
}

function isAssistantResult(value: unknown) {
  return (
    isAssistantCompletedResult(value) ||
    isAssistantPartialResult(value) ||
    isAssistantHistoryResult(value)
  );
}

function isAssistantCompletedResult(value: unknown) {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      "value",
      "messages_chain",
      "blocks",
      "context_compactions",
      "sources",
      "termination_reason",
    ]) &&
    typeof value.value === "string" &&
    isMessageChain(value.messages_chain) &&
    isRuntimeResultMetadata(value)
  );
}

function isAssistantPartialResult(value: unknown) {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      "value",
      "blocks",
      "context_compactions",
      "sources",
      "termination_reason",
    ]) &&
    typeof value.value === "string" &&
    isRuntimeResultMetadata(value)
  );
}

function isAssistantHistoryResult(value: unknown) {
  return (
    isRecord(value) &&
    (hasExactKeys(value, ["messages_chain"]) ||
      hasExactKeys(value, ["value", "messages_chain"])) &&
    (!("value" in value) || typeof value.value === "string") &&
    isMessageChain(value.messages_chain, true)
  );
}

function isRuntimeResultMetadata(value: Record<string, unknown>) {
  return (
    Array.isArray(value.blocks) &&
    value.blocks.every(isChatBlock) &&
    Array.isArray(value.context_compactions) &&
    value.context_compactions.every(isContextCompaction) &&
    Array.isArray(value.sources) &&
    value.sources.every(isJsonValue) &&
    (value.termination_reason === null || typeof value.termination_reason === "string")
  );
}

function isContextCompaction(value: unknown) {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      "message_index",
      "compacted",
      "summary_compacted",
      "version",
    ]) &&
    isNonNegativeInteger(value.message_index) &&
    typeof value.compacted === "boolean" &&
    typeof value.summary_compacted === "boolean" &&
    (value.version === null || isPositiveInteger(value.version))
  );
}

function isMessageChain(value: unknown, allowEmpty = false) {
  return (
    Array.isArray(value) &&
    (allowEmpty || value.length > 0) &&
    value.every(isMessageChainItem)
  );
}

function isMessageChainItem(value: unknown) {
  if (!isRecord(value)) return false;
  if (value.role === "tool") {
    const expected = "is_error" in value
      ? ["role", "tool_call_id", "name", "content", "is_error"]
      : ["role", "tool_call_id", "name", "content"];
    return (
      hasExactKeys(value, expected) &&
      typeof value.tool_call_id === "string" &&
      value.tool_call_id.length > 0 &&
      typeof value.name === "string" &&
      value.name.length > 0 &&
      typeof value.content === "string" &&
      (!("is_error" in value) || typeof value.is_error === "boolean")
    );
  }
  if (value.role !== "assistant") return false;
  const allowed = new Set([
    "role",
    "content",
    "tool_calls",
    "reasoning_content",
    "model_info",
    "compacted",
    "summary_compacted",
    "compaction_version",
  ]);
  return (
    Object.keys(value).every((key) => allowed.has(key)) &&
    "content" in value &&
    (value.content === null ||
      typeof value.content === "string" ||
      (Array.isArray(value.content) && value.content.every(isJsonObject))) &&
    (!("tool_calls" in value) ||
      (Array.isArray(value.tool_calls) && value.tool_calls.every(isToolCall))) &&
    (!("reasoning_content" in value) || typeof value.reasoning_content === "string") &&
    (!("model_info" in value) || isModelInfo(value.model_info)) &&
    (!("compacted" in value) || typeof value.compacted === "boolean") &&
    (!("summary_compacted" in value) || typeof value.summary_compacted === "boolean") &&
    (!("compaction_version" in value) ||
      value.compaction_version === null ||
      isPositiveInteger(value.compaction_version))
  );
}

function isToolCall(value: unknown) {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["id", "type", "function"]) &&
    typeof value.id === "string" &&
    value.id.length > 0 &&
    value.type === "function" &&
    isRecord(value.function) &&
    hasExactKeys(value.function, ["name", "arguments"]) &&
    typeof value.function.name === "string" &&
    value.function.name.length > 0 &&
    typeof value.function.arguments === "string"
  );
}

function isModelInfo(value: unknown) {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["provider", "model"]) &&
    (value.provider === null || typeof value.provider === "string") &&
    (value.model === null || typeof value.model === "string")
  );
}

function isModelRef(value: unknown) {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["provider", "model"]) &&
    typeof value.provider === "string" &&
    unicodeLength(value.provider.trim()) >= 1 &&
    unicodeLength(value.provider.trim()) <= 64 &&
    typeof value.model === "string" &&
    unicodeLength(value.model.trim()) >= 1 &&
    unicodeLength(value.model.trim()) <= 160
  );
}

function isIsoDateTime(value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(?:Z|([+-])(\d{2}):(\d{2}))?$/.exec(
    value,
  );
  if (!match) return false;
  const [, yearText, monthText, dayText, hourText, minuteText, secondText, , , offsetHourText, offsetMinuteText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  const second = Number(secondText);
  if (
    month < 1 ||
    month > 12 ||
    day < 1 ||
    day > daysInMonth(year, month) ||
    hour > 23 ||
    minute > 59 ||
    second > 59
  ) {
    return false;
  }
  if (offsetHourText !== undefined || offsetMinuteText !== undefined) {
    const offsetHour = Number(offsetHourText);
    const offsetMinute = Number(offsetMinuteText);
    if (offsetHour > 23 || offsetMinute > 59) return false;
  }
  return true;
}

function isCanonicalUtcTimestamp(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /T\d{2}:\d{2}:\d{2}(?:\.(?!000000)\d{6})?Z$/.test(value) &&
    isIsoDateTime(value)
  );
}

function isJsonObject(value: unknown): boolean {
  return isRecord(value) && isJsonValue(value);
}

function isJsonValue(value: unknown): boolean {
  type StackEntry = {
    value: unknown;
    depth: number;
    exiting?: boolean;
  };
  const stack: StackEntry[] = [{ value, depth: 0 }];
  const active = new Set<object>();
  let nodeCount = 0;
  let rawStringBytes = 0;

  while (stack.length > 0) {
    const entry = stack.pop();
    if (!entry) return false;
    const current = entry.value;
    if (entry.exiting) {
      if (typeof current === "object" && current !== null) active.delete(current);
      continue;
    }
    nodeCount += 1;
    if (nodeCount > 50_000) return false;
    if (current === null || typeof current === "boolean") continue;
    if (typeof current === "string") {
      const bytes = jsonStringBytes(current);
      if (bytes === null) return false;
      rawStringBytes += bytes;
      if (rawStringBytes > 4 * 1024 * 1024) return false;
      continue;
    }
    if (typeof current === "number") {
      if (!Number.isFinite(current)) return false;
      rawStringBytes += String(current).length;
      if (rawStringBytes > 4 * 1024 * 1024) return false;
      continue;
    }
    if (typeof current !== "object") return false;
    if (!Array.isArray(current) && !isPlainRecord(current)) return false;
    if (entry.depth >= 64 || active.has(current)) return false;
    active.add(current);
    stack.push({ value: current, depth: entry.depth, exiting: true });
    if (Array.isArray(current)) {
      for (let index = current.length - 1; index >= 0; index -= 1) {
        stack.push({ value: current[index], depth: entry.depth + 1 });
      }
      continue;
    }
    const keys = Object.keys(current);
    for (const key of keys) {
      nodeCount += 1;
      if (nodeCount > 50_000) return false;
      const bytes = jsonStringBytes(key);
      if (bytes === null) return false;
      rawStringBytes += bytes;
      if (rawStringBytes > 4 * 1024 * 1024) return false;
    }
    for (let index = keys.length - 1; index >= 0; index -= 1) {
      stack.push({ value: current[keys[index]], depth: entry.depth + 1 });
    }
  }
  try {
    const serialized = JSON.stringify(value);
    return (
      typeof serialized === "string" &&
      new TextEncoder().encode(serialized).byteLength <= 4 * 1024 * 1024
    );
  } catch {
    return false;
  }
}

function isPlainRecord(value: object): value is Record<string, unknown> {
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function jsonStringBytes(value: string): number | null {
  if (unicodeLength(value) > 1_000_000 || hasUnpairedSurrogate(value)) return null;
  return new TextEncoder().encode(value).byteLength;
}

function hasUnpairedSurrogate(value: string) {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(index + 1);
      if (next < 0xdc00 || next > 0xdfff) return true;
      index += 1;
    } else if (code >= 0xdc00 && code <= 0xdfff) {
      return true;
    }
  }
  return false;
}

function unicodeLength(value: string) {
  return Array.from(value).length;
}

function daysInMonth(year: number, month: number) {
  if (month === 2) {
    const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    return leap ? 29 : 28;
  }
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}
