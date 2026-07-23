import type {
  ActiveStreamSnapshot,
  ChatBlockUpdatedPayload,
} from "@/lib/socket-types";
import type {
  AssistantCompletedResult,
  AssistantPartialResult,
  ChatBlock,
  JsonObject,
  JsonValue,
  MessageChainItem,
  Subtask,
  SubtaskContextBrief,
  SubtaskRole,
  SubtaskStatus,
  ToolChatBlock,
} from "@/lib/types";

export interface TaskChatMessage {
  key: string;
  taskId: number | null;
  subtaskId: number | null;
  messageId: number | null;
  parentId: number | null;
  role: SubtaskRole;
  prompt: string;
  status: SubtaskStatus;
  progress: number;
  blocks: ChatBlock[];
  messagesChain: MessageChainItem[];
  contexts: SubtaskContextBrief[];
  generationId: string | null;
  streamOffset: number;
  errorCode: string | null;
  errorMessage: string | null;
  optimistic: boolean;
  createdAt: string | null;
  updatedAt: string | null;
  completedAt: string | null;
}

export interface TaskChatState {
  taskId: number | null;
  messages: TaskChatMessage[];
  needsResync: boolean;
  statusUpdated: JsonObject | null;
}

export type ChatBlockPatch = Partial<
  Pick<ChatBlockUpdatedPayload, "content" | "tool_input" | "tool_output" | "status">
>;

interface GenerationAction {
  taskId: number;
  subtaskId: number;
  generationId: string;
}

export type TaskChatAction =
  | {
      type: "reset";
      taskId: number | null;
    }
  | {
      type: "set-task-id";
      taskId: number;
    }
  | {
      type: "hydrate";
      taskId: number;
      subtasks: Subtask[];
      streaming: ActiveStreamSnapshot | null;
      replace: boolean;
    }
  | ({ type: "start" } & GenerationAction)
  | ({
      type: "chunk";
      blockId: string;
      blockOffset: number;
      offset: number;
      content: string;
    } & GenerationAction)
  | ({ type: "block-created"; block: ChatBlock } & GenerationAction)
  | ({ type: "block-updated"; blockId: string; patch: ChatBlockPatch } & GenerationAction)
  | ({ type: "done"; result: AssistantCompletedResult } & GenerationAction)
  | ({ type: "error"; code: string; result: AssistantPartialResult | null } & GenerationAction)
  | ({ type: "cancelled"; result: AssistantPartialResult | null } & GenerationAction)
  | ({ type: "status-updated"; status: JsonObject } & GenerationAction)
  | {
      type: "task-terminal";
      taskId: number;
      forceResync?: boolean;
    }
  | {
      type: "optimistic-user";
      localKey: string;
      taskId: number | null;
      prompt: string;
      contexts: SubtaskContextBrief[];
    }
  | {
      type: "optimistic-ack";
      localKey: string;
      taskId: number;
      subtaskId: number;
      messageId: number;
    }
  | { type: "optimistic-error"; localKey: string }
  | { type: "retry-pending"; taskId: number; subtaskId: number }
  | {
      type: "retry-restore";
      taskId: number;
      subtaskId: number;
      snapshot: TaskChatMessage;
    };

export function initialTaskChatState(taskId: number | null): TaskChatState {
  return {
    taskId,
    messages: [],
    needsResync: false,
    statusUpdated: null,
  };
}

export function reduceTaskChat(
  state: TaskChatState,
  action: TaskChatAction,
): TaskChatState {
  if (action.type === "reset") return initialTaskChatState(action.taskId);
  if (action.type === "set-task-id") {
    if (state.taskId !== null && state.taskId !== action.taskId) return state;
    return {
      ...state,
      taskId: action.taskId,
      messages: state.messages.map((message) =>
        message.taskId === null ? { ...message, taskId: action.taskId } : message,
      ),
    };
  }
  if (action.type === "optimistic-user") {
    if (state.taskId !== action.taskId) return state;
    return {
      ...state,
      messages: [
        ...state.messages,
        optimisticUser(
          action.localKey,
          action.taskId,
          action.prompt,
          action.contexts ?? [],
        ),
      ],
    };
  }
  if (action.type === "optimistic-ack") {
    if (state.taskId !== null && state.taskId !== action.taskId) return state;
    let found = false;
    const messages = state.messages.map((message) => {
      if (message.key !== action.localKey || !message.optimistic) return message;
      found = true;
      return {
        ...message,
        key: `subtask-${action.subtaskId}`,
        taskId: action.taskId,
        subtaskId: action.subtaskId,
        messageId: action.messageId,
        status: "COMPLETED" as const,
        progress: 100,
        optimistic: false,
      };
    });
    if (!found) return state;
    return { ...state, taskId: action.taskId, messages };
  }
  if (action.type === "optimistic-error") {
    const messages = state.messages.filter(
      (message) => message.key !== action.localKey || !message.optimistic,
    );
    return messages.length === state.messages.length ? state : { ...state, messages };
  }

  if (state.taskId !== action.taskId) return state;

  if (action.type === "task-terminal") {
    const hasMutableAssistant = state.messages.some(
      (message) =>
        message.role === "ASSISTANT" &&
        (message.status === "PENDING" || message.status === "RUNNING"),
    );
    // A Task status event carries no Assistant result. Keep the stale message
    // visibly non-terminal and fetch the authoritative in-place row instead
    // of fabricating a completed/failed answer without its result payload.
    return hasMutableAssistant || action.forceResync
      ? { ...state, needsResync: true }
      : state;
  }

  if (action.type === "hydrate") {
    return hydrateState(state, action.subtasks, action.streaming, action.replace);
  }
  if (action.type === "retry-pending") {
    return updateMessage(state, action.subtaskId, (message) => {
      if (message.role !== "ASSISTANT" || message.status !== "FAILED") return message;
      return {
        ...message,
        status: "PENDING",
        progress: 0,
        blocks: [],
        messagesChain: [],
        generationId: null,
        streamOffset: 0,
        errorCode: null,
        errorMessage: null,
        completedAt: null,
      };
    });
  }
  if (action.type === "retry-restore") {
    return updateMessage(state, action.subtaskId, (message) => {
      if (
        message.role !== "ASSISTANT" ||
        message.status !== "PENDING" ||
        message.generationId !== null
      ) {
        return message;
      }
      return cloneJson(action.snapshot);
    });
  }
  if (action.type === "start") {
    return upsertAssistant(state, action, (message) => {
      if (message.generationId === action.generationId && message.status === "RUNNING") {
        return message;
      }
      return {
        ...message,
        status: "RUNNING",
        progress: 0,
        blocks: [],
        messagesChain: [],
        generationId: action.generationId,
        streamOffset: 0,
        errorCode: null,
        errorMessage: null,
        completedAt: null,
      };
    });
  }
  if (action.type === "status-updated") {
    if (!generationMatches(state, action.subtaskId, action.generationId)) return state;
    return { ...state, statusUpdated: cloneJson(action.status) };
  }
  if (!generationMatches(state, action.subtaskId, action.generationId)) return state;

  if (action.type === "block-created") {
    return updateMessage(state, action.subtaskId, (message) => {
      if (message.blocks.some((block) => block.id === action.block.id)) return message;
      return { ...message, blocks: [...message.blocks, cloneBlock(action.block)] };
    });
  }
  if (action.type === "block-updated") {
    const message = state.messages.find((item) => item.subtaskId === action.subtaskId);
    if (!message) return state;
    const blockIndex = message.blocks.findIndex((block) => block.id === action.blockId);
    if (blockIndex < 0) return { ...state, needsResync: true };
    const block = message.blocks[blockIndex];
    const nextBlock = patchBlock(block, action.patch);
    if (nextBlock === block) return state;
    const blocks = [...message.blocks];
    blocks[blockIndex] = nextBlock;
    return replaceMessage(state, message, { ...message, blocks });
  }
  if (action.type === "chunk") {
    return appendChunk(state, action);
  }
  if (action.type === "done") {
    return updateMessage(state, action.subtaskId, (message) => ({
      ...message,
      status: "COMPLETED",
      progress: 100,
      blocks: cloneBlocks(action.result.blocks),
      messagesChain: cloneJson(action.result.messages_chain),
      generationId: null,
      streamOffset: action.result.value.length,
      errorCode: null,
      errorMessage: null,
      completedAt: message.completedAt,
    }));
  }
  if (action.type === "error" || action.type === "cancelled") {
    const status = action.type === "error" ? "FAILED" : "CANCELLED";
    return updateMessage(state, action.subtaskId, (message) => ({
      ...message,
      status,
      progress: 100,
      blocks: action.result ? cloneBlocks(action.result.blocks) : message.blocks,
      generationId: null,
      streamOffset: action.result?.value.length ?? message.streamOffset,
      errorCode: action.type === "error" ? action.code : null,
      errorMessage: action.type === "error" ? action.code : null,
      completedAt: message.completedAt,
    }));
  }
  return state;
}

export function selectBlocks(
  state: TaskChatState,
  subtaskId: number,
): readonly ChatBlock[] {
  return state.messages.find((message) => message.subtaskId === subtaskId)?.blocks ?? [];
}

export function maxDurableMessageId(state: TaskChatState): number {
  return state.messages.reduce(
    (maximum, message) =>
      !message.optimistic && message.messageId !== null
        ? Math.max(maximum, message.messageId)
        : maximum,
    0,
  );
}

/**
 * Return the strict `message_id > cursor` reconnect boundary.
 *
 * A PENDING/RUNNING Assistant is durable in MySQL but not stable: the backend
 * completes that same row in place. Keeping its ID in the cursor would hide
 * the terminal replacement after Redis has already finalized the stream.
 */
export function reconnectCursorMessageId(state: TaskChatState): number {
  const firstMutableAssistantId = state.messages.reduce<number | null>(
    (minimum, message) => {
      if (
        message.optimistic ||
        message.role !== "ASSISTANT" ||
        !isMutableStatus(message.status) ||
        message.messageId === null
      ) {
        return minimum;
      }
      return minimum === null
        ? message.messageId
        : Math.min(minimum, message.messageId);
    },
    null,
  );
  return state.messages.reduce((maximum, message) => {
    if (
      message.optimistic ||
      message.messageId === null ||
      isMutableStatus(message.status) ||
      (firstMutableAssistantId !== null && message.messageId >= firstMutableAssistantId)
    ) {
      return maximum;
    }
    return Math.max(maximum, message.messageId);
  }, 0);
}

function isMutableStatus(status: SubtaskStatus): boolean {
  return status === "PENDING" || status === "RUNNING";
}

function hydrateState(
  state: TaskChatState,
  subtasks: Subtask[],
  streaming: ActiveStreamSnapshot | null,
  replace: boolean,
): TaskChatState {
  const durable = subtasks.map(messageFromSubtask);
  const optimistic = state.messages.filter((message) => message.optimistic);
  let messages: TaskChatMessage[];
  if (replace) {
    messages = [...durable, ...optimistic];
  } else {
    const replacements = new Map(
      durable.map((message) => [message.subtaskId, message] as const),
    );
    messages = state.messages.map((message) =>
      message.subtaskId !== null && replacements.has(message.subtaskId)
        ? replacements.get(message.subtaskId)!
        : message,
    );
    const existingIds = new Set(messages.map((message) => message.subtaskId));
    messages.push(...durable.filter((message) => !existingIds.has(message.subtaskId)));
  }
  messages = sortMessages(messages);

  let statusUpdated = replace ? null : state.statusUpdated;
  let needsResync = false;
  if (streaming) {
    const existingIndex = messages.findIndex(
      (message) => message.subtaskId === streaming.subtask_id,
    );
    const existing =
      existingIndex >= 0
        ? messages[existingIndex]
        : emptyAssistant(streaming.task_id, streaming.subtask_id);
    const reconciled = reconcileSnapshotBlocks(streaming);
    const blocks = reconciled.blocks;
    needsResync = reconciled.needsResync;
    const active: TaskChatMessage = {
      ...existing,
      taskId: streaming.task_id,
      subtaskId: streaming.subtask_id,
      key: `subtask-${streaming.subtask_id}`,
      status: "RUNNING",
      progress: Math.max(existing.progress, 0),
      blocks,
      generationId: streaming.generation_id,
      streamOffset: streaming.offset,
      errorCode: null,
      errorMessage: null,
      completedAt: null,
    };
    if (existingIndex >= 0) messages[existingIndex] = active;
    else messages.push(active);
    statusUpdated = streaming.status_updated ? cloneJson(streaming.status_updated) : null;
  }
  return {
    ...state,
    messages: sortMessages(messages),
    needsResync,
    statusUpdated,
  };
}

function reconcileSnapshotBlocks(snapshot: ActiveStreamSnapshot): {
  blocks: ChatBlock[];
  needsResync: boolean;
} {
  const blocks = cloneBlocks(snapshot.blocks);
  const cached = snapshot.cached_content;
  if (cached.length !== snapshot.offset) {
    return { blocks, needsResync: true };
  }
  const currentIndex = findCurrentTextBlock(blocks);
  const streamingTextCount = blocks.filter(
    (block) => block.type === "text" && block.status === "streaming",
  ).length;
  if (
    streamingTextCount > 1 ||
    (currentIndex >= 0 && currentIndex !== blocks.length - 1)
  ) {
    return { blocks, needsResync: true };
  }
  let cachedOffset = 0;
  for (const [index, block] of blocks.entries()) {
    if (block.type !== "text") continue;
    if (index === currentIndex) {
      const remaining = cached.slice(cachedOffset);
      if (!remaining.startsWith(block.content)) {
        return { blocks, needsResync: true };
      }
      blocks[index] = { ...block, content: remaining };
      cachedOffset = cached.length;
      continue;
    }
    const end = cachedOffset + block.content.length;
    if (cached.slice(cachedOffset, end) !== block.content) {
      return { blocks, needsResync: true };
    }
    cachedOffset = end;
  }
  if (cachedOffset !== cached.length) {
    // A non-empty cache without a current server block has no authoritative
    // block ID. Fabricating one would make the next chunk impossible to merge.
    return { blocks, needsResync: true };
  }
  return { blocks, needsResync: false };
}

function findCurrentTextBlock(blocks: readonly ChatBlock[]): number {
  for (let index = blocks.length - 1; index >= 0; index -= 1) {
    const block = blocks[index];
    if (block?.type === "text" && block.status === "streaming") return index;
  }
  return -1;
}

function appendChunk(
  state: TaskChatState,
  action: Extract<TaskChatAction, { type: "chunk" }>,
): TaskChatState {
  const message = state.messages.find((item) => item.subtaskId === action.subtaskId);
  if (!message) return state;
  const blockIndex = message.blocks.findIndex((block) => block.id === action.blockId);
  if (blockIndex < 0) {
    if (action.blockOffset !== 0 || action.offset !== message.streamOffset) {
      return { ...state, needsResync: true };
    }
    const blocks = [
      ...message.blocks,
      {
        id: action.blockId,
        type: "text" as const,
        content: action.content,
        status: "streaming" as const,
        timestamp: message.updatedAt ?? "",
      },
    ];
    return replaceMessage(state, message, {
      ...message,
      blocks,
      streamOffset: action.offset + action.content.length,
    });
  }
  const block = message.blocks[blockIndex];
  if (block.type !== "text") return { ...state, needsResync: true };
  if (action.blockOffset < block.content.length || action.offset < message.streamOffset) {
    return state;
  }
  if (action.blockOffset > block.content.length || action.offset > message.streamOffset) {
    return { ...state, needsResync: true };
  }
  const blocks = [...message.blocks];
  blocks[blockIndex] = { ...block, content: block.content + action.content };
  return replaceMessage(state, message, {
    ...message,
    blocks,
    streamOffset: message.streamOffset + action.content.length,
  });
}

function generationMatches(
  state: TaskChatState,
  subtaskId: number,
  generationId: string,
): boolean {
  const message = state.messages.find((item) => item.subtaskId === subtaskId);
  return message?.generationId === generationId;
}

function upsertAssistant(
  state: TaskChatState,
  action: GenerationAction,
  transform: (message: TaskChatMessage) => TaskChatMessage,
): TaskChatState {
  const existing = state.messages.find((message) => message.subtaskId === action.subtaskId);
  if (existing) return replaceMessage(state, existing, transform(existing));
  const created = transform(emptyAssistant(action.taskId, action.subtaskId));
  return { ...state, messages: sortMessages([...state.messages, created]) };
}

function updateMessage(
  state: TaskChatState,
  subtaskId: number,
  transform: (message: TaskChatMessage) => TaskChatMessage,
): TaskChatState {
  const message = state.messages.find((item) => item.subtaskId === subtaskId);
  if (!message) return state;
  const updated = transform(message);
  return updated === message ? state : replaceMessage(state, message, updated);
}

function replaceMessage(
  state: TaskChatState,
  current: TaskChatMessage,
  replacement: TaskChatMessage,
): TaskChatState {
  return {
    ...state,
    messages: state.messages.map((message) =>
      message === current ? replacement : message,
    ),
  };
}

function patchBlock(block: ChatBlock, patch: ChatBlockPatch): ChatBlock {
  if (block.type === "text") {
    const content = patch.content !== undefined && patch.content !== null
      ? patch.content
      : block.content;
    const status = patch.status === "streaming" || patch.status === "done"
      ? patch.status
      : block.status;
    if (content === block.content && status === block.status) return block;
    return {
      ...block,
      content,
      status,
    };
  }
  const status = patch.status && patch.status !== "streaming"
    ? patch.status
    : block.status;
  const toolInput = patch.tool_input ?? block.tool_input;
  const hasOutput = Object.prototype.hasOwnProperty.call(patch, "tool_output") &&
    (status === "done" || status === "error");
  const next = {
    ...block,
    tool_input: cloneJson(toolInput),
    ...(hasOutput ? { tool_output: cloneJson(patch.tool_output) as JsonValue } : {}),
    status,
  };
  return jsonEqual(block, next) ? block : next;
}

function messageFromSubtask(subtask: Subtask): TaskChatMessage {
  const messagesChain = subtask.result?.messages_chain
    ? cloneJson(subtask.result.messages_chain)
    : [];
  const storedBlocks = resultBlocks(subtask.result);
  const blocks = subtask.role === "ASSISTANT"
    ? storedBlocks.length
      ? storedBlocks
      : blocksFromHistory(subtask, messagesChain)
    : [];
  return {
    key: `subtask-${subtask.id}`,
    taskId: subtask.task_id,
    subtaskId: subtask.id,
    messageId: subtask.message_id,
    parentId: subtask.parent_id,
    role: subtask.role,
    prompt: subtask.prompt,
    status: subtask.status,
    progress: subtask.progress,
    blocks,
    messagesChain,
    contexts: cloneJson(subtask.contexts),
    generationId: null,
    streamOffset: resultValue(subtask.result).length,
    errorCode: subtask.error_message,
    errorMessage: subtask.error_message,
    optimistic: false,
    createdAt: subtask.created_at,
    updatedAt: subtask.updated_at,
    completedAt: subtask.completed_at,
  };
}

function blocksFromHistory(
  subtask: Subtask,
  chain: MessageChainItem[],
): ChatBlock[] {
  const blocks: ChatBlock[] = [];
  const tools = new Map<string, number>();
  for (const [messageIndex, message] of chain.entries()) {
    if (message.role === "assistant") {
      const content = visibleChainContent(message.content);
      if (content) {
        blocks.push({
          id: `history-${subtask.id}-${messageIndex}-text`,
          type: "text",
          content,
          status: "done",
          timestamp: subtask.updated_at,
        });
      }
      for (const [callIndex, call] of (message.tool_calls ?? []).entries()) {
        const block: ToolChatBlock = {
          id: `history-${subtask.id}-${messageIndex}-${callIndex}-${call.id}`,
          type: "tool",
          tool_use_id: call.id,
          tool_name: call.function.name,
          tool_input: parseArguments(call.function.arguments),
          status: "pending",
          timestamp: subtask.updated_at,
        };
        tools.set(call.id, blocks.length);
        blocks.push(block);
      }
    } else {
      const blockIndex = tools.get(message.tool_call_id);
      if (blockIndex === undefined) continue;
      const current = blocks[blockIndex];
      if (current?.type !== "tool") continue;
      blocks[blockIndex] = {
        ...current,
        tool_output: message.content,
        status: message.is_error ? "error" : "done",
      };
    }
  }
  const fallback = resultValue(subtask.result);
  if (blocks.length === 0 && fallback) {
    blocks.push({
      id: `history-${subtask.id}-value`,
      type: "text",
      content: fallback,
      status: "done",
      timestamp: subtask.updated_at,
    });
  }
  return blocks;
}

function visibleChainContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return JSON.stringify(content);
  return "";
}

function parseArguments(value: string): JsonObject {
  try {
    const parsed: unknown = JSON.parse(value);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as JsonObject;
    }
  } catch {
    // The server normally canonicalizes arguments; preserve invalid history as data.
  }
  return { arguments: value };
}

function resultBlocks(result: Subtask["result"]): ChatBlock[] {
  if (!result || !("blocks" in result) || !Array.isArray(result.blocks)) return [];
  return cloneBlocks(result.blocks);
}

function resultValue(result: Subtask["result"]): string {
  return result && "value" in result && typeof result.value === "string"
    ? result.value
    : "";
}

function emptyAssistant(taskId: number, subtaskId: number): TaskChatMessage {
  return {
    key: `subtask-${subtaskId}`,
    taskId,
    subtaskId,
    messageId: null,
    parentId: null,
    role: "ASSISTANT",
    prompt: "",
    status: "PENDING",
    progress: 0,
    blocks: [],
    messagesChain: [],
    contexts: [],
    generationId: null,
    streamOffset: 0,
    errorCode: null,
    errorMessage: null,
    optimistic: false,
    createdAt: null,
    updatedAt: null,
    completedAt: null,
  };
}

function optimisticUser(
  localKey: string,
  taskId: number | null,
  prompt: string,
  contexts: SubtaskContextBrief[],
): TaskChatMessage {
  return {
    key: localKey,
    taskId,
    subtaskId: null,
    messageId: null,
    parentId: null,
    role: "USER",
    prompt,
    status: "PENDING",
    progress: 0,
    blocks: [],
    messagesChain: [],
    contexts: contexts.map((context) => ({
      ...context,
      type_data: cloneJson(context.type_data),
    })),
    generationId: null,
    streamOffset: 0,
    errorCode: null,
    errorMessage: null,
    optimistic: true,
    createdAt: null,
    updatedAt: null,
    completedAt: null,
  };
}

function sortMessages(messages: TaskChatMessage[]): TaskChatMessage[] {
  return [...messages].sort((left, right) => {
    if (left.messageId === null && right.messageId === null) return 0;
    if (left.messageId === null) return 1;
    if (right.messageId === null) return -1;
    return left.messageId - right.messageId;
  });
}

function cloneBlocks(blocks: readonly ChatBlock[]): ChatBlock[] {
  return blocks.map(cloneBlock);
}

function cloneBlock(block: ChatBlock): ChatBlock {
  if (block.type === "text") return { ...block };
  return {
    ...block,
    tool_input: cloneJson(block.tool_input),
    ...(Object.prototype.hasOwnProperty.call(block, "tool_output")
      ? { tool_output: cloneJson(block.tool_output) }
      : {}),
  };
}

function cloneJson<T>(value: T): T {
  if (value === undefined || value === null) return value;
  return JSON.parse(JSON.stringify(value)) as T;
}

function jsonEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}
