import type {
  AssistantCompletedResult,
  AssistantPartialResult,
  ChatBlock,
  JsonObject,
  JsonValue,
  ModelRef,
  Subtask,
  TaskAgentResponse,
  TaskStatus,
} from "./types";

export interface TaskJoinPayload {
  task_id: number;
  after_message_id?: number | null;
}

export interface TaskLeavePayload {
  task_id: number;
}

export interface ChatSendPayload {
  task_id?: number | null;
  message: string;
  agent_id?: string | null;
  model_override?: ModelRef | null;
  context_ids?: number[];
}

export interface ChatCancelPayload {
  task_id: number;
}

export interface ChatRetryPayload {
  task_id: number;
  subtask_id: number;
}

export interface ActiveStreamSnapshot {
  task_id: number;
  subtask_id: number;
  generation_id: string;
  /** JavaScript UTF-16 code-unit offset. */
  offset: number;
  cached_content: string;
  blocks: ChatBlock[];
  started_at: string;
  last_activity_at: string;
  status_updated: JsonObject | null;
}

export interface TaskJoinAck {
  task_id: number;
  subtasks: Subtask[];
  streaming: ActiveStreamSnapshot | null;
}

export interface TaskLeaveAck {
  task_id: number;
}

export interface ChatSendAck {
  task_id: number;
  /** Durable User Subtask ID. */
  subtask_id: number;
  message_id: number;
}

export interface ChatCancelAck {
  task_id: number;
  subtask_id: number | null;
  accepted: boolean;
}

export interface ChatRetryAck {
  task_id: number;
  /** The same Assistant Subtask ID supplied in the retry request. */
  subtask_id: number;
}

export interface SocketErrorAck {
  error: {
    code: string;
  };
}

export type SocketAck<T> = T | SocketErrorAck;

interface GenerationEvent {
  task_id: number;
  subtask_id: number;
  generation_id: string;
}

export interface ChatStartPayload extends GenerationEvent {
  status: "RUNNING";
}

export interface ChatChunkPayload extends GenerationEvent {
  block_id: string;
  /** JavaScript UTF-16 code-unit offset within the block. */
  block_offset: number;
  /** JavaScript UTF-16 code-unit offset within the visible answer. */
  offset: number;
  content: string;
}

export interface ChatBlockCreatedPayload extends GenerationEvent {
  block: ChatBlock;
}

export interface ChatBlockUpdatedPayload extends GenerationEvent {
  block_id: string;
  content: string | null;
  tool_input: JsonObject | null;
  tool_output: JsonValue | null;
  status: "generating_arguments" | "pending" | "streaming" | "done" | "error" | null;
}

export interface ChatDonePayload extends GenerationEvent {
  result: AssistantCompletedResult;
}

export interface ChatErrorPayload extends GenerationEvent {
  code: string;
  result: AssistantPartialResult | null;
}

export interface ChatCancelledPayload extends GenerationEvent {
  result: AssistantPartialResult | null;
}

export interface ChatStatusUpdatedPayload extends GenerationEvent {
  status: JsonObject;
}

export interface TaskBrief {
  id: number;
  name: string;
  href: string;
  status: TaskStatus;
  agent: TaskAgentResponse;
  model_override: ModelRef | null;
  created_at: string;
  updated_at: string;
}

export interface TaskCreatedPayload {
  task: TaskBrief;
}

export interface TaskStatusPayload {
  task: TaskBrief;
}

type AckCallback<T> = (response: SocketAck<T>) => void;

export interface ClientToServerEvents {
  "task:join": (payload: TaskJoinPayload, ack: AckCallback<TaskJoinAck>) => void;
  "task:leave": (payload: TaskLeavePayload, ack: AckCallback<TaskLeaveAck>) => void;
  "chat:send": (payload: ChatSendPayload, ack: AckCallback<ChatSendAck>) => void;
  "chat:cancel": (payload: ChatCancelPayload, ack: AckCallback<ChatCancelAck>) => void;
  "chat:retry": (payload: ChatRetryPayload, ack: AckCallback<ChatRetryAck>) => void;
}

export interface ServerToClientEvents {
  "chat:start": (payload: ChatStartPayload) => void;
  "chat:chunk": (payload: ChatChunkPayload) => void;
  "chat:block_created": (payload: ChatBlockCreatedPayload) => void;
  "chat:block_updated": (payload: ChatBlockUpdatedPayload) => void;
  "chat:done": (payload: ChatDonePayload) => void;
  "chat:error": (payload: ChatErrorPayload) => void;
  "chat:cancelled": (payload: ChatCancelledPayload) => void;
  "chat:status_updated": (payload: ChatStatusUpdatedPayload) => void;
  "task:created": (payload: TaskCreatedPayload) => void;
  "task:status": (payload: TaskStatusPayload) => void;
}

export type SocketEventName = keyof ServerToClientEvents;
export type SocketEventHandlers = Partial<ServerToClientEvents>;
