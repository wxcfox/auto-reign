import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatWorkspace } from "../ChatWorkspace";
import type { TaskChatMessage } from "@/components/chat/task-chat-reducer";
import { useTaskChat } from "@/components/chat/useTaskChat";
import i18next from "@/i18n/setup";
import {
  getModels,
  getTask,
  listAgents,
  listSubtaskContextDrafts,
  setTaskModel,
} from "@/lib/api";
import type { Agent, SubtaskContextBrief, TaskDetailResponse } from "@/lib/types";

const navigation = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
vi.mock("@/components/chat/useTaskChat", () => ({ useTaskChat: vi.fn() }));
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

const timestamp = "2026-07-22T00:00:00Z";
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
const agent: Agent = {
  id: "agent-1",
  name: "Research agent",
  scope: "private",
  can_manage: true,
  is_active: true,
  config: {
    system_prompt: "Research",
    default_model: { provider: "qwen", model: "plus" },
    home_workspace_id: null,
    knowledge_scopes: [],
  },
  created_at: timestamp,
  updated_at: timestamp,
};
const task: TaskDetailResponse = {
  id: 7,
  name: "Existing Task",
  href: "/chat?task=7",
  agent: { id: agent.id, name: agent.name, is_available: true },
  model_override: null,
  status: "COMPLETED",
  created_at: timestamp,
  updated_at: timestamp,
  last_message: "answer",
  subtasks: [],
};

function userMessage(overrides: Partial<TaskChatMessage> = {}): TaskChatMessage {
  return {
    key: "subtask-1",
    taskId: 7,
    subtaskId: 1,
    messageId: 1,
    parentId: null,
    role: "USER",
    prompt: "question",
    status: "COMPLETED",
    progress: 100,
    blocks: [],
    messagesChain: [],
    contexts: [context],
    generationId: null,
    streamOffset: 0,
    errorCode: null,
    errorMessage: null,
    optimistic: false,
    createdAt: timestamp,
    updatedAt: timestamp,
    completedAt: timestamp,
    ...overrides,
  };
}

const send = vi.fn();
const cancelTask = vi.fn();
const retryAssistant = vi.fn();
let hookState: ReturnType<typeof useTaskChat>;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function makeHookState(overrides: Partial<ReturnType<typeof useTaskChat>> = {}) {
  return {
    taskId: null,
    createdTaskId: null,
    taskStatus: null,
    messages: [],
    statusUpdated: null,
    connected: true,
    loading: false,
    reconnecting: false,
    sending: false,
    cancelling: false,
    retryingSubtaskId: null,
    errorCode: null,
    send,
    cancelTask,
    retryAssistant,
    clearError: vi.fn(),
    ...overrides,
  } as ReturnType<typeof useTaskChat>;
}

describe("ChatWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    void i18next.changeLanguage("en");
    hookState = makeHookState();
    vi.mocked(useTaskChat).mockImplementation(() => hookState);
    vi.mocked(listAgents).mockResolvedValue({ agents: [agent] });
    vi.mocked(getModels).mockResolvedValue({
      providers: [{ provider: "qwen", models: ["plus"] }],
      default: { provider: "qwen", model: "plus" },
    });
    vi.mocked(getTask).mockResolvedValue(task);
    vi.mocked(listSubtaskContextDrafts).mockResolvedValue([]);
    vi.mocked(setTaskModel).mockResolvedValue(task);
    send.mockResolvedValue({ task_id: 9, subtask_id: 41, message_id: 51 });
    cancelTask.mockResolvedValue({ task_id: 7, subtask_id: 2, accepted: true });
    retryAssistant.mockResolvedValue({ task_id: 7, subtask_id: 2 });
  });

  it("renders a joined Task snapshot including durable User Contexts", async () => {
    hookState = makeHookState({ taskId: 7, messages: [userMessage()] });
    render(<ChatWorkspace taskId={7} />);

    expect(await screen.findByText("Existing Task")).toBeInTheDocument();
    expect(screen.getByText("question")).toBeInTheDocument();
    expect(screen.getByText("source.txt")).toBeInTheDocument();
    expect(getTask).toHaveBeenCalledWith(7);
    expect(useTaskChat).toHaveBeenCalledWith(7);
  });

  it("sends through the Task hook, binds the draft snapshot, clears it after ACK, and routes", async () => {
    vi.mocked(listSubtaskContextDrafts).mockResolvedValue([context]);
    render(<ChatWorkspace />);
    const textbox = await screen.findByRole("textbox", { name: "Message Auto Reign" });
    await screen.findByText("source.txt");
    fireEvent.change(textbox, { target: { value: "Use this file" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));

    await waitFor(() => expect(send).toHaveBeenCalledWith("Use this file", {
      agentId: null,
      modelOverride: null,
      contextIds: [31],
      contexts: [context],
    }));
    expect(navigation.replace).toHaveBeenCalledWith("/chat?task=9", { scroll: false });
    expect(screen.queryByText("source.txt")).not.toBeInTheDocument();
  });

  it("renders block created/updated tool output and final answer state", async () => {
    hookState = makeHookState({
      taskId: 7,
      messages: [
        userMessage({
          key: "subtask-2",
          subtaskId: 2,
          role: "ASSISTANT",
          prompt: "",
          contexts: [],
          blocks: [
            {
              id: "call-1",
              type: "tool",
              tool_use_id: "call-1",
              tool_name: "knowledge_search",
              tool_input: { query: "q" },
              tool_output: "hit",
              status: "done",
              timestamp,
            },
            { id: "text-1", type: "text", content: "final answer", status: "done", timestamp },
          ],
        }),
      ],
    });
    render(<ChatWorkspace taskId={7} />);

    expect(await screen.findByText("knowledge_search · Done")).toBeInTheDocument();
    fireEvent.click(screen.getByText("knowledge_search · Done"));
    expect(screen.getByText("hit")).toBeInTheDocument();
    expect(screen.getByText("final answer")).toBeInTheDocument();
  });

  it("disables send while RUNNING and cancels the active Task", async () => {
    hookState = makeHookState({
      taskId: 7,
      messages: [userMessage({
        key: "subtask-2",
        subtaskId: 2,
        role: "ASSISTANT",
        prompt: "",
        contexts: [],
        status: "RUNNING",
        completedAt: null,
      })],
    });
    render(<ChatWorkspace taskId={7} />);
    expect(await screen.findByRole("button", { name: "Send message" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Stop generation" }));
    await waitFor(() => expect(cancelTask).toHaveBeenCalledTimes(1));
  });

  it.each(["PENDING", "RUNNING"] as const)(
    "disables send for REST Task status %s before an Assistant snapshot exists",
    async (status) => {
      vi.mocked(getTask).mockResolvedValue({ ...task, status });
      hookState = makeHookState({ taskId: 7, taskStatus: null, messages: [] });
      render(<ChatWorkspace taskId={7} />);
      const textbox = await screen.findByRole("textbox", { name: "Message Auto Reign" });
      expect(textbox).toBeDisabled();
      expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
      expect(screen.getByText("This Task is running.")).toBeInTheDocument();
    },
  );

  it("prefers a terminal socket Task status over a stale RUNNING REST detail", async () => {
    vi.mocked(getTask).mockResolvedValue({ ...task, status: "RUNNING" });
    hookState = makeHookState({ taskId: 7, taskStatus: "COMPLETED", messages: [] });
    render(<ChatWorkspace taskId={7} />);
    const textbox = await screen.findByRole("textbox", { name: "Message Auto Reign" });
    fireEvent.change(textbox, { target: { value: "next turn" } });
    expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
    expect(screen.queryByText("This Task is running.")).not.toBeInTheDocument();
  });

  it("retries a FAILED Assistant in place using the same Subtask ID", async () => {
    hookState = makeHookState({
      taskId: 7,
      messages: [userMessage({
        key: "subtask-2",
        subtaskId: 2,
        role: "ASSISTANT",
        prompt: "",
        contexts: [],
        status: "FAILED",
        errorCode: "provider_call_failed",
      })],
    });
    render(<ChatWorkspace taskId={7} />);
    fireEvent.click(await screen.findByRole("button", { name: "Retry response" }));
    await waitFor(() => expect(retryAssistant).toHaveBeenCalledWith(2));
  });

  it("fails closed while disconnected and shows reconnect recovery state", async () => {
    hookState = makeHookState({ connected: false, reconnecting: true });
    render(<ChatWorkspace />);
    expect(await screen.findByText("Reconnecting and recovering the Task stream...")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message Auto Reign" })).toBeDisabled();
  });

  it("cleans the old Task view contract when the task prop changes", async () => {
    const view = render(<ChatWorkspace taskId={7} />);
    await screen.findByText("Existing Task");
    vi.mocked(getTask).mockResolvedValue({ ...task, id: 8, name: "Next Task", href: "/chat?task=8" });
    view.rerender(<ChatWorkspace taskId={8} />);
    expect(await screen.findByText("Next Task")).toBeInTheDocument();
    expect(useTaskChat).toHaveBeenLastCalledWith(8);
    expect(listSubtaskContextDrafts).toHaveBeenCalledTimes(2);
  });

  it("does not let a stale model response overwrite a newer Task view", async () => {
    const oldUpdate = deferred<TaskDetailResponse>();
    vi.mocked(setTaskModel).mockReturnValue(oldUpdate.promise);
    vi.mocked(getTask).mockImplementation(async (id) => ({
      ...task,
      id,
      name: id === 7 ? "Existing Task" : "Next Task",
      href: `/chat?task=${id}`,
    }));
    const view = render(<ChatWorkspace taskId={7} />);
    await screen.findByText("Existing Task");
    fireEvent.click(screen.getByRole("button", { name: "Select model" }));
    fireEvent.click(screen.getByRole("option", { name: "plus" }));
    await waitFor(() => expect(setTaskModel).toHaveBeenCalledWith(7, {
      provider: "qwen",
      model: "plus",
    }));

    view.rerender(<ChatWorkspace taskId={8} />);
    await screen.findByText("Next Task");
    oldUpdate.resolve({
      ...task,
      name: "Stale polluted Task",
      model_override: { provider: "stale", model: "stale-model" },
    });
    await oldUpdate.promise;

    await waitFor(() => expect(screen.queryByText("Stale polluted Task")).not.toBeInTheDocument());
    expect(screen.getByText("Next Task")).toBeInTheDocument();
    expect(screen.queryByText("stale / stale-model")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message Auto Reign" })).toBeEnabled();
  });

  it("keeps bound Context cleanup but ignores stale new-Task ACK navigation and UI", async () => {
    const pendingSend = deferred<{ task_id: number; subtask_id: number; message_id: number }>();
    send.mockReturnValue(pendingSend.promise);
    vi.mocked(listSubtaskContextDrafts).mockResolvedValue([context]);
    const view = render(<ChatWorkspace />);
    await screen.findByText("source.txt");
    fireEvent.change(screen.getByRole("textbox", { name: "Message Auto Reign" }), {
      target: { value: "old view message" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));

    view.rerender(<ChatWorkspace taskId={7} />);
    await screen.findByText("Existing Task");
    const currentComposer = screen.getByRole("textbox", { name: "Message Auto Reign" });
    fireEvent.change(currentComposer, { target: { value: "current view message" } });
    pendingSend.resolve({ task_id: 9, subtask_id: 41, message_id: 51 });
    await pendingSend.promise;

    await waitFor(() => expect(screen.queryByText("source.txt")).not.toBeInTheDocument());
    expect(navigation.replace).not.toHaveBeenCalled();
    expect(currentComposer).toHaveValue("current view message");
  });

  it("does not restore a rejected old send into the new Task composer", async () => {
    const pendingSend = deferred<{ task_id: number; subtask_id: number; message_id: number }>();
    send.mockReturnValue(pendingSend.promise);
    const view = render(<ChatWorkspace />);
    const oldComposer = await screen.findByRole("textbox", { name: "Message Auto Reign" });
    fireEvent.change(oldComposer, { target: { value: "old failed message" } });
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    await waitFor(() => expect(send).toHaveBeenCalledTimes(1));

    view.rerender(<ChatWorkspace taskId={7} />);
    await screen.findByText("Existing Task");
    const currentComposer = screen.getByRole("textbox", { name: "Message Auto Reign" });
    fireEvent.change(currentComposer, { target: { value: "new view draft" } });
    pendingSend.reject(new Error("socket failed"));
    await waitFor(() => expect(currentComposer).toHaveValue("new view draft"));
    expect(navigation.replace).not.toHaveBeenCalled();
  });
});
