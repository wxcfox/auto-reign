"use client";

import {
  useCallback,
  useEffect,
  useReducer,
  useRef,
  useState,
} from "react";

import { useSocket } from "@/contexts/SocketContext";
import type {
  ChatSendAck,
  ChatSendPayload,
  SocketEventHandlers,
  TaskJoinAck,
} from "@/lib/socket-types";
import type { ModelRef, SubtaskContextBrief, TaskStatus } from "@/lib/types";

import {
  initialTaskChatState,
  reconnectCursorMessageId,
  reduceTaskChat,
  type TaskChatAction,
} from "./task-chat-reducer";

const UNCERTAIN_OPERATION_FAILURES = new Set([
  "socket_disconnected",
  "socket_timeout",
  "socket_unmounted",
  "malformed_ack",
  "socket_request_failed",
]);

export interface SendTaskChatOptions {
  agentId?: string | null;
  modelOverride?: ModelRef | null;
  contextIds?: number[];
  contexts?: SubtaskContextBrief[];
}

export class TaskChatOperationError extends Error {
  constructor(public readonly code: string) {
    super(`Task chat operation failed: ${code}`);
    this.name = "TaskChatOperationError";
  }
}

interface JoinHydrationAttempt {
  readonly token: number;
  readonly epoch: number;
  readonly taskId: number;
  actions: TaskChatAction[];
  taskStatuses: Map<number, TaskStatus>;
}

interface AwaitingAssistantLifecycle {
  readonly taskId: number;
  readonly userMessageId: number;
}

export function useTaskChat(initialTaskId: number | null) {
  const {
    connected,
    joinTask,
    leaveTask,
    sendChatMessage,
    cancel,
    retry,
    registerHandlers,
    onReconnect,
  } = useSocket();
  const [state, dispatch] = useReducer(
    reduceTaskChat,
    initialTaskId,
    initialTaskChatState,
  );
  const [loading, setLoading] = useState(initialTaskId !== null);
  const [reconnecting, setReconnecting] = useState(false);
  const [sending, setSending] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [retryingSubtaskId, setRetryingSubtaskId] = useState<number | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);
  const [createdTaskId, setCreatedTaskId] = useState<number | null>(null);
  const [taskStatus, setTaskStatus] = useState<TaskStatus | null>(null);

  const stateRef = useRef(state);
  const effectiveTaskIdRef = useRef<number | null>(initialTaskId);
  const hydrationReadyRef = useRef(initialTaskId === null);
  const preAdoptionActionsRef = useRef<TaskChatAction[]>([]);
  const preAdoptionTaskStatusesRef = useRef(new Map<number, TaskStatus>());
  const activeJoinAttemptRef = useRef<JoinHydrationAttempt | null>(null);
  const joinAttemptTokenRef = useRef(0);
  const joinInFlightRef = useRef(false);
  const resyncRef = useRef<(() => void) | null>(null);
  const initialJoinRef = useRef<(() => void) | null>(null);
  const hasHydratedRef = useRef(false);
  const roomJoinedRef = useRef(false);
  const lifecycleActiveRef = useRef(false);
  const sendInFlightRef = useRef(false);
  const cancelInFlightRef = useRef(false);
  const retryInFlightRef = useRef(new Set<number>());
  const operationEpochRef = useRef(0);
  const optimisticCounterRef = useRef(0);
  const taskStatusRef = useRef<TaskStatus | null>(null);
  const awaitingAssistantRef = useRef<AwaitingAssistantLifecycle | null>(null);
  const terminalBeforeSendAckRef = useRef(new Set<number>());
  const assistantLifecycleBeforeSendAckRef = useRef(new Set<number>());

  const updateTaskStatus = useCallback((status: TaskStatus | null) => {
    taskStatusRef.current = status;
    setTaskStatus(status);
  }, []);

  const dispatchTaskAction = useCallback((action: TaskChatAction) => {
    if (action.type === "start") {
      const awaitingAssistant = awaitingAssistantRef.current;
      const existingAssistant = stateRef.current.messages.find(
        (message) =>
          message.role === "ASSISTANT" &&
          message.subtaskId === action.subtaskId,
      );
      if (
        awaitingAssistant?.taskId === action.taskId &&
        (existingAssistant === undefined ||
          existingAssistant.parentId === awaitingAssistant.userMessageId)
      ) {
        awaitingAssistantRef.current = null;
      }
    }
    const terminalTaskId =
      action.type === "done" ||
      action.type === "error" ||
      action.type === "cancelled"
        ? action.taskId
        : null;
    const terminalWithoutConsumableStart =
      terminalTaskId !== null &&
      awaitingAssistantRef.current?.taskId === terminalTaskId;
    if (action.type !== "task-terminal") {
      dispatch(action);
      if (terminalWithoutConsumableStart) {
        dispatch({
          type: "task-terminal",
          taskId: terminalTaskId,
          forceResync: true,
        });
      }
      return;
    }
    const awaitingAssistant = awaitingAssistantRef.current;
    dispatch({
      ...action,
      forceResync:
        action.forceResync || awaitingAssistant?.taskId === action.taskId,
    });
  }, []);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    const epoch = operationEpochRef.current + 1;
    operationEpochRef.current = epoch;
    effectiveTaskIdRef.current = initialTaskId;
    hydrationReadyRef.current = initialTaskId === null;
    preAdoptionActionsRef.current = [];
    preAdoptionTaskStatusesRef.current.clear();
    activeJoinAttemptRef.current = null;
    joinInFlightRef.current = false;
    hasHydratedRef.current = false;
    roomJoinedRef.current = false;
    lifecycleActiveRef.current = true;
    sendInFlightRef.current = false;
    cancelInFlightRef.current = false;
    retryInFlightRef.current.clear();
    awaitingAssistantRef.current = null;
    terminalBeforeSendAckRef.current.clear();
    assistantLifecycleBeforeSendAckRef.current.clear();
    stateRef.current = initialTaskChatState(initialTaskId);
    dispatch({ type: "reset", taskId: initialTaskId });
    setCreatedTaskId(null);
    setErrorCode(null);
    setLoading(initialTaskId !== null);
    setReconnecting(false);
    setSending(false);
    setCancelling(false);
    setRetryingSubtaskId(null);
    updateTaskStatus(null);
    let alive = true;
    let initialJoinStarted = false;

    const dispatchEvent = (action: TaskChatAction) => {
      if (!("taskId" in action) || action.type === "reset") return;
      const effectiveTaskId = effectiveTaskIdRef.current;
      if (effectiveTaskId !== null && action.taskId !== effectiveTaskId) return;
      if (effectiveTaskId === null) {
        if (sendInFlightRef.current) preAdoptionActionsRef.current.push(action);
        return;
      }
      if (!hydrationReadyRef.current) {
        const attempt = activeJoinAttemptRef.current;
        if (attempt?.epoch === epoch && attempt.taskId === action.taskId) {
          attempt.actions.push(action);
        }
        return;
      }
      dispatchTaskAction(action);
    };

    const acceptTaskStatus = (taskId: number, status: TaskStatus) => {
      const effectiveTaskId = effectiveTaskIdRef.current;
      if (effectiveTaskId === taskId) {
        if (!hydrationReadyRef.current) {
          const attempt = activeJoinAttemptRef.current;
          if (attempt?.epoch === epoch && attempt.taskId === taskId) {
            attempt.taskStatuses.set(taskId, status);
          }
        } else {
          updateTaskStatus(status);
        }
        return;
      }
      if (effectiveTaskId === null && sendInFlightRef.current) {
        preAdoptionTaskStatusesRef.current.set(taskId, status);
      }
    };

    const acceptTerminalTaskStatus = (
      taskId: number,
      status: "COMPLETED" | "FAILED" | "CANCELLED",
    ) => {
      if (
        sendInFlightRef.current &&
        awaitingAssistantRef.current === null &&
        effectiveTaskIdRef.current === taskId
      ) {
        terminalBeforeSendAckRef.current.add(taskId);
      }
      acceptTaskStatus(taskId, status);
      dispatchEvent({ type: "task-terminal", taskId });
    };

    const recordAssistantLifecycleBeforeAck = (
      taskId: number,
      subtaskId: number,
    ) => {
      const existingAssistant = stateRef.current.messages.some(
        (message) =>
          message.role === "ASSISTANT" && message.subtaskId === subtaskId,
      );
      if (
        sendInFlightRef.current &&
        awaitingAssistantRef.current === null &&
        !existingAssistant
      ) {
        assistantLifecycleBeforeSendAckRef.current.add(taskId);
      }
    };

    const handlers: SocketEventHandlers = {
      "chat:start": (payload) => {
        recordAssistantLifecycleBeforeAck(payload.task_id, payload.subtask_id);
        acceptTaskStatus(payload.task_id, "RUNNING");
        dispatchEvent({
          type: "start",
          taskId: payload.task_id,
          subtaskId: payload.subtask_id,
          generationId: payload.generation_id,
        });
      },
      "chat:chunk": (payload) =>
        dispatchEvent({
          type: "chunk",
          taskId: payload.task_id,
          subtaskId: payload.subtask_id,
          generationId: payload.generation_id,
          blockId: payload.block_id,
          blockOffset: payload.block_offset,
          offset: payload.offset,
          content: payload.content,
        }),
      "chat:block_created": (payload) =>
        dispatchEvent({
          type: "block-created",
          taskId: payload.task_id,
          subtaskId: payload.subtask_id,
          generationId: payload.generation_id,
          block: payload.block,
        }),
      "chat:block_updated": (payload) =>
        dispatchEvent({
          type: "block-updated",
          taskId: payload.task_id,
          subtaskId: payload.subtask_id,
          generationId: payload.generation_id,
          blockId: payload.block_id,
          patch: {
            content: payload.content,
            tool_input: payload.tool_input,
            tool_output: payload.tool_output,
            status: payload.status,
          },
        }),
      "chat:done": (payload) => {
        acceptTaskStatus(payload.task_id, "COMPLETED");
        dispatchEvent({
          type: "done",
          taskId: payload.task_id,
          subtaskId: payload.subtask_id,
          generationId: payload.generation_id,
          result: payload.result,
        });
      },
      "chat:error": (payload) => {
        acceptTaskStatus(payload.task_id, "FAILED");
        dispatchEvent({
          type: "error",
          taskId: payload.task_id,
          subtaskId: payload.subtask_id,
          generationId: payload.generation_id,
          code: payload.code,
          result: payload.result,
        });
      },
      "chat:cancelled": (payload) => {
        acceptTaskStatus(payload.task_id, "CANCELLED");
        dispatchEvent({
          type: "cancelled",
          taskId: payload.task_id,
          subtaskId: payload.subtask_id,
          generationId: payload.generation_id,
          result: payload.result,
        });
      },
      "chat:status_updated": (payload) =>
        dispatchEvent({
          type: "status-updated",
          taskId: payload.task_id,
          subtaskId: payload.subtask_id,
          generationId: payload.generation_id,
          status: payload.status,
        }),
      "task:created": ({ task }) => {
        if (isTerminalTaskStatus(task.status)) {
          acceptTerminalTaskStatus(task.id, task.status);
        } else {
          acceptTaskStatus(task.id, task.status);
        }
      },
      "task:status": ({ task }) => {
        if (isTerminalTaskStatus(task.status)) {
          acceptTerminalTaskStatus(task.id, task.status);
        } else {
          acceptTaskStatus(task.id, task.status);
        }
      },
    };

    const cleanupHandlers = registerHandlers(handlers);

    const joinAndHydrate = async (
      taskId: number,
      afterMessageId: number | null,
      replace: boolean,
      force: boolean,
    ) => {
      if (joinInFlightRef.current) return false;
      joinInFlightRef.current = true;
      hydrationReadyRef.current = false;
      const attempt: JoinHydrationAttempt = {
        token: ++joinAttemptTokenRef.current,
        epoch,
        taskId,
        actions: [],
        taskStatuses: new Map(),
      };
      activeJoinAttemptRef.current = attempt;
      const ownsAttempt = () => {
        const activeAttempt = activeJoinAttemptRef.current;
        return (
          activeAttempt?.token === attempt.token &&
          activeAttempt.epoch === attempt.epoch &&
          activeAttempt.taskId === attempt.taskId
        );
      };
      if (!replace) setReconnecting(true);
      try {
        const options = force
          ? { afterMessageId, force: true as const }
          : { afterMessageId };
        const ack = await joinTask(taskId, options);
        if (!alive || operationEpochRef.current !== epoch) {
          attempt.actions = [];
          attempt.taskStatuses.clear();
          const newerLifecycleOwnsRoom =
            lifecycleActiveRef.current && effectiveTaskIdRef.current === taskId;
          if (!newerLifecycleOwnsRoom) {
            void leaveTask(taskId).catch(() => undefined);
          }
          return false;
        }
        if (!ownsAttempt()) {
          attempt.actions = [];
          attempt.taskStatuses.clear();
          return false;
        }
        if (ack.task_id !== taskId) {
          throw new TaskChatOperationError("malformed_ack");
        }
        const awaitingAssistant = awaitingAssistantRef.current;
        if (
          awaitingAssistant?.taskId === taskId &&
          ack.subtasks.some(
            (subtask) =>
              subtask.role === "ASSISTANT" &&
              subtask.parent_id === awaitingAssistant.userMessageId,
          )
        ) {
          awaitingAssistantRef.current = null;
        }
        const joinedTaskStatus = taskStatusFromJoin(ack);
        if (joinedTaskStatus !== null) updateTaskStatus(joinedTaskStatus);
        dispatch({
          type: "hydrate",
          taskId,
          subtasks: ack.subtasks,
          streaming: ack.streaming,
          replace,
        });
        hydrationReadyRef.current = true;
        const queued = attempt.actions;
        attempt.actions = [];
        for (const action of queued) {
          if ("taskId" in action && action.taskId === taskId) {
            dispatchTaskAction(action);
          }
        }
        const queuedTaskStatus = attempt.taskStatuses.get(taskId);
        if (queuedTaskStatus !== undefined) {
          updateTaskStatus(queuedTaskStatus);
        }
        attempt.taskStatuses.clear();
        activeJoinAttemptRef.current = null;
        hasHydratedRef.current = true;
        roomJoinedRef.current = true;
        setErrorCode(null);
        return true;
      } catch (error) {
        attempt.actions = [];
        attempt.taskStatuses.clear();
        const code = safeErrorCode(error);
        if (
          alive &&
          operationEpochRef.current === epoch &&
          ownsAttempt()
        ) {
          setErrorCode(code);
          if (isUncertainOperationFailure(code)) {
            roomJoinedRef.current = false;
            try {
              await leaveTask(taskId);
            } catch {
              // The original join error remains authoritative.
            }
            attempt.actions = [];
            attempt.taskStatuses.clear();
          }
          if (ownsAttempt()) {
            activeJoinAttemptRef.current = null;
          }
        } else if (isUncertainOperationFailure(code)) {
          const newerLifecycleOwnsRoom =
            lifecycleActiveRef.current && effectiveTaskIdRef.current === taskId;
          if (!newerLifecycleOwnsRoom) {
            try {
              await leaveTask(taskId);
            } catch {
              // A stale attempt must not surface cleanup failures in a new lifecycle.
            }
          }
        }
        return false;
      } finally {
        if (
          alive &&
          operationEpochRef.current === epoch &&
          (activeJoinAttemptRef.current === null ||
            activeJoinAttemptRef.current === attempt)
        ) {
          joinInFlightRef.current = false;
          setLoading(false);
          setReconnecting(false);
        }
      }
    };

    const cleanupReconnect = onReconnect(() => {
      const taskId = effectiveTaskIdRef.current;
      if (taskId === null) return;
      void joinAndHydrate(
        taskId,
        reconnectCursorMessageId(stateRef.current),
        !hasHydratedRef.current,
        true,
      );
    });

    resyncRef.current = () => {
      const taskId = effectiveTaskIdRef.current;
      if (taskId === null) return;
      setReconnecting(true);
      void joinAndHydrate(taskId, null, true, true);
    };

    initialJoinRef.current = () => {
      if (initialTaskId === null || initialJoinStarted || hasHydratedRef.current) return;
      initialJoinStarted = true;
      void joinAndHydrate(initialTaskId, null, true, false).then((joined) => {
        if (!joined && alive && operationEpochRef.current === epoch) {
          initialJoinStarted = false;
        }
      });
    };

    return () => {
      alive = false;
      lifecycleActiveRef.current = false;
      operationEpochRef.current += 1;
      const activeAttempt = activeJoinAttemptRef.current;
      if (activeAttempt?.epoch === epoch) {
        activeAttempt.actions = [];
        activeAttempt.taskStatuses.clear();
        activeJoinAttemptRef.current = null;
      }
      resyncRef.current = null;
      initialJoinRef.current = null;
      cleanupHandlers();
      cleanupReconnect();
      const taskId = effectiveTaskIdRef.current;
      if (taskId !== null && roomJoinedRef.current) {
        roomJoinedRef.current = false;
        void leaveTask(taskId).catch(() => undefined);
      }
    };
  }, [
    initialTaskId,
    joinTask,
    leaveTask,
    onReconnect,
    registerHandlers,
    updateTaskStatus,
    dispatchTaskAction,
  ]);

  useEffect(() => {
    if (connected && initialTaskId !== null) initialJoinRef.current?.();
  }, [connected, initialTaskId]);

  useEffect(() => {
    if (state.needsResync) resyncRef.current?.();
  }, [state.needsResync]);

  const send = useCallback(
    async (message: string, options: SendTaskChatOptions = {}): Promise<ChatSendAck> => {
      if (sendInFlightRef.current) {
        throw new TaskChatOperationError("task_send_pending");
      }
      const epoch = operationEpochRef.current;
      const taskId = effectiveTaskIdRef.current;
      const previousTaskStatus = taskStatusRef.current;
      const localKey = `optimistic-${++optimisticCounterRef.current}`;
      sendInFlightRef.current = true;
      updateTaskStatus("PENDING");
      setSending(true);
      setErrorCode(null);
      dispatch({
        type: "optimistic-user",
        localKey,
        taskId,
        prompt: message,
        contexts: options.contexts ?? [],
      });
      const payload: ChatSendPayload = { task_id: taskId, message };
      if (options.agentId !== undefined) payload.agent_id = options.agentId;
      if (options.modelOverride !== undefined) {
        payload.model_override = options.modelOverride;
      }
      if (options.contextIds !== undefined) payload.context_ids = options.contextIds;
      try {
        const ack = await sendChatMessage(payload);
        if (operationEpochRef.current === epoch) {
          effectiveTaskIdRef.current = ack.task_id;
          const lifecycleAlreadyObserved =
            assistantLifecycleBeforeSendAckRef.current.delete(ack.task_id);
          awaitingAssistantRef.current = lifecycleAlreadyObserved
            ? null
            : {
                taskId: ack.task_id,
                userMessageId: ack.message_id,
              };
          updateTaskStatus(
            preAdoptionTaskStatusesRef.current.get(ack.task_id) ?? "PENDING",
          );
          preAdoptionTaskStatusesRef.current.delete(ack.task_id);
          roomJoinedRef.current = true;
          hasHydratedRef.current = true;
          dispatch({
            type: "optimistic-ack",
            localKey,
            taskId: ack.task_id,
            subtaskId: ack.subtask_id,
            messageId: ack.message_id,
          });
          if (
            terminalBeforeSendAckRef.current.delete(ack.task_id) &&
            !lifecycleAlreadyObserved
          ) {
            dispatchTaskAction({
              type: "task-terminal",
              taskId: ack.task_id,
              forceResync: true,
            });
          }
          if (taskId === null) {
            setCreatedTaskId(ack.task_id);
            const queued = preAdoptionActionsRef.current;
            preAdoptionActionsRef.current = [];
            for (const action of queued) {
              if ("taskId" in action && action.taskId === ack.task_id) {
                dispatchTaskAction(action);
              }
            }
          }
        }
        return ack;
      } catch (error) {
        const code = safeErrorCode(error);
        if (operationEpochRef.current === epoch) {
          dispatch({ type: "optimistic-error", localKey });
          setErrorCode(code);
          preAdoptionActionsRef.current = [];
          preAdoptionTaskStatusesRef.current.clear();
          if (!isUncertainOperationFailure(code)) {
            terminalBeforeSendAckRef.current.clear();
            assistantLifecycleBeforeSendAckRef.current.clear();
            updateTaskStatus(previousTaskStatus);
          }
        }
        throw new TaskChatOperationError(code);
      } finally {
        if (operationEpochRef.current === epoch) {
          sendInFlightRef.current = false;
          setSending(false);
        }
      }
    },
    [dispatchTaskAction, sendChatMessage, updateTaskStatus],
  );

  const cancelTask = useCallback(async () => {
    const taskId = effectiveTaskIdRef.current;
    if (taskId === null) throw new TaskChatOperationError("task_not_found");
    if (cancelInFlightRef.current) {
      throw new TaskChatOperationError("task_cancel_pending");
    }
    const epoch = operationEpochRef.current;
    cancelInFlightRef.current = true;
    setCancelling(true);
    setErrorCode(null);
    try {
      return await cancel(taskId);
    } catch (error) {
      const code = safeErrorCode(error);
      if (operationEpochRef.current === epoch) setErrorCode(code);
      throw new TaskChatOperationError(code);
    } finally {
      if (operationEpochRef.current === epoch) {
        cancelInFlightRef.current = false;
        setCancelling(false);
      }
    }
  }, [cancel]);

  const retryAssistant = useCallback(
    async (subtaskId: number) => {
      const taskId = effectiveTaskIdRef.current;
      if (taskId === null) throw new TaskChatOperationError("task_not_found");
      if (retryInFlightRef.current.has(subtaskId)) {
        throw new TaskChatOperationError("task_retry_pending");
      }
      const epoch = operationEpochRef.current;
      const previousTaskStatus = taskStatusRef.current;
      const retrySnapshot = stateRef.current.messages.find(
        (message) =>
          message.subtaskId === subtaskId &&
          message.role === "ASSISTANT" &&
          message.status === "FAILED",
      );
      retryInFlightRef.current.add(subtaskId);
      updateTaskStatus("PENDING");
      setRetryingSubtaskId(subtaskId);
      setErrorCode(null);
      const pendingAction = { type: "retry-pending", taskId, subtaskId } as const;
      stateRef.current = reduceTaskChat(stateRef.current, pendingAction);
      dispatch(pendingAction);
      try {
        const ack = await retry(taskId, subtaskId);
        return ack;
      } catch (error) {
        const code = safeErrorCode(error);
        if (operationEpochRef.current === epoch) {
          if (retrySnapshot && !isUncertainOperationFailure(code)) {
            const restoreAction = {
              type: "retry-restore",
              taskId,
              subtaskId,
              snapshot: retrySnapshot,
            } as const;
            stateRef.current = reduceTaskChat(stateRef.current, restoreAction);
            dispatch(restoreAction);
          }
          if (!isUncertainOperationFailure(code)) {
            updateTaskStatus(previousTaskStatus);
          }
          setErrorCode(code);
        }
        throw new TaskChatOperationError(code);
      } finally {
        retryInFlightRef.current.delete(subtaskId);
        if (operationEpochRef.current === epoch) setRetryingSubtaskId(null);
      }
    },
    [retry, updateTaskStatus],
  );

  return {
    taskId: state.taskId,
    createdTaskId,
    taskStatus,
    messages: state.messages,
    statusUpdated: state.statusUpdated,
    connected,
    loading,
    reconnecting,
    sending,
    cancelling,
    retryingSubtaskId,
    errorCode,
    send,
    cancelTask,
    retryAssistant,
    clearError: () => setErrorCode(null),
  };
}

function safeErrorCode(error: unknown): string {
  if (
    error &&
    typeof error === "object" &&
    "code" in error &&
    typeof error.code === "string" &&
    /^[a-z][a-z0-9_]{0,127}$/.test(error.code)
  ) {
    return error.code;
  }
  return "socket_request_failed";
}

function isUncertainOperationFailure(code: string): boolean {
  return UNCERTAIN_OPERATION_FAILURES.has(code);
}

function taskStatusFromJoin(ack: TaskJoinAck): TaskStatus | null {
  if (ack.streaming !== null) return "RUNNING";
  for (let index = ack.subtasks.length - 1; index >= 0; index -= 1) {
    const subtask = ack.subtasks[index];
    if (subtask?.role === "ASSISTANT") return subtask.status;
  }
  return null;
}

function isTerminalTaskStatus(
  status: TaskStatus,
): status is "COMPLETED" | "FAILED" | "CANCELLED" {
  return status === "COMPLETED" || status === "FAILED" || status === "CANCELLED";
}
