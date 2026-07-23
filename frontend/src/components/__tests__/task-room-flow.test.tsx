import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatWorkspace } from "../ChatWorkspace";
import {
  SocketProvider,
  type ChatSocket,
  type SocketFactory,
} from "@/contexts/SocketContext";
import i18next from "@/i18n/setup";
import { setAuthToken } from "@/lib/auth";
import {
  getModels,
  getTask,
  listAgents,
  listSubtaskContextDrafts,
  setTaskModel,
} from "@/lib/api";
import type {
  Subtask,
  SubtaskContextBrief,
  TaskDetailResponse,
} from "@/lib/types";

const navigation = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
vi.mock("@/lib/api", () => ({
  deleteSubtaskContextDraft: vi.fn(),
  getModels: vi.fn(),
  getTask: vi.fn(),
  listAgents: vi.fn(),
  listSubtaskContextDrafts: vi.fn(),
  readSubtaskContextContent: vi.fn(),
  setTaskModel: vi.fn(),
  uploadSubtaskContext: vi.fn(),
}));

type Listener = (...args: unknown[]) => void;
type Response = unknown | ((payload: unknown) => unknown | Promise<unknown>);

class FlowSocket {
  connected = false;
  emitted: Array<{ event: string; payload: unknown }> = [];
  private listeners = new Map<string, Set<Listener>>();
  private responses = new Map<string, Response[]>();

  on(event: string, listener: Listener) {
    const listeners = this.listeners.get(event) ?? new Set<Listener>();
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

  timeout() {
    return this;
  }

  emitWithAck(event: string, payload: unknown) {
    this.emitted.push({ event, payload });
    const queue = this.responses.get(event) ?? [];
    const response = queue.shift();
    this.responses.set(event, queue);
    return Promise.resolve(
      typeof response === "function" ? response(payload) : response,
    );
  }

  connect() {
    this.connected = true;
    this.trigger("connect");
    return this;
  }

  disconnect() {
    this.connected = false;
    return this;
  }

  respond(event: string, ...responses: Response[]) {
    this.responses.set(event, responses);
  }

  trigger(event: string, payload?: unknown) {
    for (const listener of this.listeners.get(event) ?? []) listener(payload);
  }

  reconnect() {
    this.connected = false;
    this.trigger("disconnect", "transport close");
    this.connected = true;
    this.trigger("connect");
  }
}

const timestamp = "2026-07-22T00:00:00Z";
const context: SubtaskContextBrief = {
  id: 31,
  context_type: "attachment",
  name: "flow-source.txt",
  status: "ready",
  mime_type: "text/plain",
  file_extension: ".txt",
  file_size: 6,
  text_length: 6,
  type_data: {},
};
const task: TaskDetailResponse = {
  id: 7,
  name: "Socket flow",
  href: "/chat?task=7",
  agent: { id: null, name: "No agent", is_available: true },
  model_override: null,
  status: "COMPLETED",
  created_at: timestamp,
  updated_at: timestamp,
  last_message: "existing question",
  subtasks: [],
};

function subtask(
  overrides: Partial<Subtask> & Pick<Subtask, "id" | "role" | "message_id">,
): Subtask {
  const { id, role, message_id, ...rest } = overrides;
  return {
    id,
    task_id: 7,
    role,
    message_id,
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

function completedResult(value: string, blocks: Array<Record<string, unknown>>) {
  return {
    value,
    blocks,
    messages_chain: [{ role: "assistant", content: value }],
    context_compactions: [],
    sources: [],
    termination_reason: null,
  };
}

function renderFlow(socket: FlowSocket) {
  const factory = vi.fn(() => socket as unknown as ChatSocket) as SocketFactory;
  return render(
    <SocketProvider socketFactory={factory} ackTimeoutMs={500}>
      <ChatWorkspace taskId={7} />
    </SocketProvider>,
  );
}

describe("Task room component flow", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    setAuthToken("task-room-flow-token");
    void i18next.changeLanguage("en");
    vi.mocked(listAgents).mockResolvedValue({ agents: [] });
    vi.mocked(getModels).mockResolvedValue({
      providers: [{ provider: "qwen", models: ["qwen3.7-plus"] }],
      default: { provider: "qwen", model: "qwen3.7-plus" },
    });
    vi.mocked(getTask).mockResolvedValue(task);
    vi.mocked(listSubtaskContextDrafts).mockResolvedValue([context]);
    vi.mocked(setTaskModel).mockResolvedValue(task);
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("hydrates an active join, then renders ACK-ordered blocks and reconnects from the cursor", async () => {
    const socket = new FlowSocket();
    socket.respond(
      "task:join",
      {
        task_id: 7,
        subtasks: [
          subtask({
            id: 1,
            role: "USER",
            message_id: 4,
            prompt: "existing question",
          }),
        ],
        streaming: {
          task_id: 7,
          subtask_id: 8,
          generation_id: "generation-active",
          offset: 9,
          cached_content: "recovered",
          blocks: [
            {
              id: "text-active",
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
      },
      { task_id: 7, subtasks: [], streaming: null },
    );
    socket.respond("chat:send", {
      task_id: 7,
      subtask_id: 10,
      message_id: 5,
    });
    socket.respond("task:leave", { task_id: 7 });
    renderFlow(socket);

    expect(await screen.findByText("existing question")).toBeInTheDocument();
    expect(screen.getByText("recovered")).toBeInTheDocument();
    expect(screen.getByText("flow-source.txt")).toBeInTheDocument();

    act(() => {
      socket.trigger("chat:done", {
        task_id: 7,
        subtask_id: 8,
        generation_id: "generation-active",
        result: completedResult("recovered", [
          {
            id: "text-active",
            type: "text",
            content: "recovered",
            status: "done",
            timestamp,
          },
        ]),
      });
    });

    const textbox = await screen.findByRole("textbox", { name: "Message Auto Reign" });
    await waitFor(() => expect(textbox).toBeEnabled());
    fireEvent.change(textbox, { target: { value: "use tool" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(socket.emitted).toContainEqual({
      event: "chat:send",
      payload: {
        task_id: 7,
        message: "use tool",
        context_ids: [31],
      },
    }));
    await waitFor(() => expect(screen.getByText("No draft Contexts.")).toBeInTheDocument());
    expect(screen.getAllByText("flow-source.txt")).toHaveLength(1);
    expect(screen.queryByText("integration_lookup · Done")).not.toBeInTheDocument();

    act(() => {
      socket.trigger("chat:start", {
        task_id: 7,
        subtask_id: 11,
        generation_id: "generation-tool",
        status: "RUNNING",
      });
      socket.trigger("chat:block_created", {
        task_id: 7,
        subtask_id: 11,
        generation_id: "generation-tool",
        block: {
          id: "tool-block-1",
          type: "tool",
          tool_use_id: "call-1",
          tool_name: "integration_lookup",
          tool_input: { query: "mysql redis" },
          status: "pending",
          timestamp,
        },
      });
      socket.trigger("chat:block_updated", {
        task_id: 7,
        subtask_id: 11,
        generation_id: "generation-tool",
        block_id: "tool-block-1",
        content: null,
        tool_input: { query: "mysql redis" },
        tool_output: "integration hit",
        status: "done",
      });
      socket.trigger("chat:block_created", {
        task_id: 7,
        subtask_id: 11,
        generation_id: "generation-tool",
        block: {
          id: "text-block-1",
          type: "text",
          content: "",
          status: "streaming",
          timestamp,
        },
      });
      socket.trigger("chat:chunk", {
        task_id: 7,
        subtask_id: 11,
        generation_id: "generation-tool",
        block_id: "text-block-1",
        block_offset: 0,
        offset: 0,
        content: "final answer",
      });
      socket.trigger("chat:block_updated", {
        task_id: 7,
        subtask_id: 11,
        generation_id: "generation-tool",
        block_id: "text-block-1",
        content: "final answer",
        tool_input: null,
        tool_output: null,
        status: "done",
      });
      socket.trigger("chat:done", {
        task_id: 7,
        subtask_id: 11,
        generation_id: "generation-tool",
        result: completedResult("final answer", [
          {
            id: "tool-block-1",
            type: "tool",
            tool_use_id: "call-1",
            tool_name: "integration_lookup",
            tool_input: { query: "mysql redis" },
            tool_output: "integration hit",
            status: "done",
            timestamp,
          },
          {
            id: "text-block-1",
            type: "text",
            content: "final answer",
            status: "done",
            timestamp,
          },
        ]),
      });
    });

    expect(await screen.findByText("integration_lookup · Done")).toBeInTheDocument();
    fireEvent.click(screen.getByText("integration_lookup · Done"));
    expect(screen.getByText("integration hit")).toBeInTheDocument();
    expect(screen.getByText("final answer")).toBeInTheDocument();

    act(() => socket.reconnect());
    await waitFor(() => expect(socket.emitted).toContainEqual({
      event: "task:join",
      payload: { task_id: 7, after_message_id: 5 },
    }));
    expect(screen.getByText("final answer")).toBeInTheDocument();
  });

  it("retries a failed Assistant in place and renders the same Subtask ID", async () => {
    const socket = new FlowSocket();
    const failedResult = {
      value: "partial answer",
      blocks: [
        {
          id: "failed-text",
          type: "text",
          content: "partial answer",
          status: "done",
          timestamp,
        },
      ],
      context_compactions: [],
      sources: [],
      termination_reason: null,
    };
    socket.respond("task:join", {
      task_id: 7,
      subtasks: [
        subtask({ id: 1, role: "USER", message_id: 4, prompt: "retry me" }),
        subtask({
          id: 9,
          role: "ASSISTANT",
          message_id: 5,
          parent_id: 4,
          status: "FAILED",
          result: failedResult,
          error_message: "provider_call_failed",
        }),
      ],
      streaming: null,
    });
    socket.respond("chat:retry", { task_id: 7, subtask_id: 9 });
    socket.respond("task:leave", { task_id: 7 });
    vi.mocked(getTask).mockResolvedValue({ ...task, status: "FAILED" });
    vi.mocked(listSubtaskContextDrafts).mockResolvedValue([]);
    renderFlow(socket);

    expect(await screen.findByText("partial answer")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry response" }));
    await waitFor(() => expect(socket.emitted).toContainEqual({
      event: "chat:retry",
      payload: { task_id: 7, subtask_id: 9 },
    }));

    act(() => {
      socket.trigger("chat:start", {
        task_id: 7,
        subtask_id: 9,
        generation_id: "generation-retry",
        status: "RUNNING",
      });
      socket.trigger("chat:block_created", {
        task_id: 7,
        subtask_id: 9,
        generation_id: "generation-retry",
        block: {
          id: "retry-text",
          type: "text",
          content: "",
          status: "streaming",
          timestamp,
        },
      });
      socket.trigger("chat:chunk", {
        task_id: 7,
        subtask_id: 9,
        generation_id: "generation-retry",
        block_id: "retry-text",
        block_offset: 0,
        offset: 0,
        content: "retry complete",
      });
      socket.trigger("chat:done", {
        task_id: 7,
        subtask_id: 9,
        generation_id: "generation-retry",
        result: completedResult("retry complete", [
          {
            id: "retry-text",
            type: "text",
            content: "retry complete",
            status: "done",
            timestamp,
          },
        ]),
      });
    });

    expect(await screen.findByText("retry complete")).toBeInTheDocument();
    expect(screen.queryByText("partial answer")).not.toBeInTheDocument();
    expect(socket.emitted.filter(({ event }) => event === "chat:retry")).toHaveLength(1);
  });
});
