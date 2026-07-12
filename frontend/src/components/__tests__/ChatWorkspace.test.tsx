import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { type ComponentProps, type ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatWorkspace } from "../ChatWorkspace";
import { ModelPicker } from "../ModelPicker";
import i18next from "@/i18n/setup";
import {
  getConversation,
  getModels,
  deleteAttachmentDraft,
  listAttachmentDrafts,
  listAgents,
  sendConversationStream,
  setConversationModel,
  uploadAttachment,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type {
  Agent,
  Attachment,
  ConversationDetailResponse,
  ConversationMessage,
  ModelRef,
} from "@/lib/types";

const navigationMocks = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMocks,
}));

vi.mock("@/lib/api", () => ({
  deleteAttachmentDraft: vi.fn(),
  getConversation: vi.fn(),
  getModels: vi.fn(),
  listAttachmentDrafts: vi.fn(),
  listAgents: vi.fn(),
  sendConversationStream: vi.fn(),
  setConversationModel: vi.fn(),
  uploadAttachment: vi.fn(),
}));

const globalAgent: Agent = {
  id: "agent-global",
  name: "Interview coach",
  scope: "global",
  can_manage: false,
  is_active: true,
  config: {
    system_prompt: "Coach the user.",
    default_model: { provider: "qwen", model: "qwen3.7-plus" },
    home_workspace_id: null,
    knowledge_scopes: [],
  },
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const privateAgent: Agent = {
  ...globalAgent,
  id: "agent-private",
  name: "Private research agent",
  scope: "private",
  can_manage: true,
};

const userMessage: ConversationMessage = {
  id: "message-user",
  role: "user",
  status: "completed",
  content: "Explain context managers.",
  attachments: [],
  provider: null,
  model: null,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
  metadata: {},
};

const assistantMessage: ConversationMessage = {
  id: "message-assistant",
  role: "assistant",
  status: "completed",
  content: "Context managers reliably release resources.",
  attachments: [],
  provider: "qwen",
  model: "qwen3.7-plus",
  created_at: "2026-07-13T00:00:01Z",
  updated_at: "2026-07-13T00:00:02Z",
  metadata: {},
};

function attachment(id: string, filename = `${id}.txt`): Attachment {
  return {
    id,
    filename,
    mime_type: "text/plain",
    size_bytes: 3,
    message_id: null,
    created_at: "2026-07-13T00:00:00Z",
  };
}

const existingConversation: ConversationDetailResponse = {
  id: "conversation-existing",
  title: "Context managers",
  href: "/chat?session=conversation-existing",
  agent: {
    id: globalAgent.id,
    name: globalAgent.name,
    is_available: true,
  },
  model_override: null,
  status: "idle",
  started_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:02Z",
  last_message: assistantMessage.content,
  messages: [userMessage, assistantMessage],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function renderWorkspace(ui: ReactElement = <ChatWorkspace />) {
  return render(ui);
}

async function enterMessage(text = "Hello") {
  const input = await screen.findByLabelText(/message auto reign/i);
  fireEvent.change(input, { target: { value: text } });
  return input;
}

async function chooseModel(name: string | RegExp) {
  fireEvent.click(screen.getByRole("button", { name: /select model/i }));
  fireEvent.click(await screen.findByRole("option", { name }));
}

describe("ChatWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    i18next.changeLanguage("en");
    vi.mocked(listAgents).mockResolvedValue({ agents: [globalAgent, privateAgent] });
    vi.mocked(listAttachmentDrafts).mockResolvedValue([]);
    vi.mocked(deleteAttachmentDraft).mockResolvedValue();
    vi.mocked(getModels).mockResolvedValue({
      providers: [
        { provider: "qwen", models: ["qwen3.7-plus", "qwen3.7-max"] },
        { provider: "local-runtime", models: ["custom-chat"] },
      ],
      default: { provider: "qwen", model: "qwen3.7-plus" },
    });
    vi.mocked(getConversation).mockResolvedValue(existingConversation);
    vi.mocked(setConversationModel).mockImplementation(async (_id, modelOverride) => ({
      ...existingConversation,
      model_override: modelOverride,
    }));
    vi.mocked(sendConversationStream).mockImplementation(async (payload, callbacks) => {
      callbacks.onAccepted?.({
        conversation_id: payload.conversation_id ?? "conversation-new",
        user_message_id: "message-user-new",
        assistant_message_id: assistantMessage.id,
        attachment_ids: payload.attachment_ids ?? [],
      });
      callbacks.onDelta(assistantMessage.content);
      return { conversation_id: "conversation-new", message: assistantMessage };
    });
  });

  it("loads visible agents and models together and defaults to the first agent", async () => {
    const agentsRequest = deferred<{ agents: Agent[] }>();
    const modelsRequest = deferred<Awaited<ReturnType<typeof getModels>>>();
    vi.mocked(listAgents).mockReturnValue(agentsRequest.promise);
    vi.mocked(getModels).mockReturnValue(modelsRequest.promise);

    renderWorkspace();

    expect(listAgents).toHaveBeenCalledWith("visible");
    expect(getModels).toHaveBeenCalledTimes(1);
    agentsRequest.resolve({ agents: [globalAgent, privateAgent] });
    modelsRequest.resolve({
      providers: [{ provider: "qwen", models: ["qwen3.7-plus"] }],
      default: { provider: "qwen", model: "qwen3.7-plus" },
    });

    expect(await screen.findByRole("button", { name: globalAgent.name })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /select model/i })).toHaveTextContent(
      /follow agent default/i,
    );
    expect(getConversation).not.toHaveBeenCalled();
  });

  it("requires an agent before send while keeping the real attachment action available", async () => {
    vi.mocked(listAgents).mockResolvedValue({ agents: [] });
    renderWorkspace();

    await enterMessage();

    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /^attach files$/i })).toBeEnabled();
    expect(sendConversationStream).not.toHaveBeenCalled();
  });

  it("keeps model selection available but disables send when no model can resolve", async () => {
    vi.mocked(listAgents).mockResolvedValue({
      agents: [{ ...globalAgent, config: { ...globalAgent.config, default_model: null } }],
    });
    vi.mocked(getModels).mockResolvedValue({ providers: [], default: null });
    renderWorkspace();

    const input = await enterMessage("Do not create a guaranteed failed turn");

    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /select model/i })).toBeEnabled();
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.submit(screen.getByRole("form", { name: /chat composer/i }));
    expect(sendConversationStream).not.toHaveBeenCalled();
  });

  it("sends the exact first-turn payload, locks the agent, routes, and refreshes history", async () => {
    const historyChanged = vi.fn();
    window.addEventListener("auto-reign:conversations-changed", historyChanged);
    const view = renderWorkspace();
    const { container } = view;
    await screen.findByRole("button", { name: globalAgent.name });
    await enterMessage("Explain Python context managers.");

    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() =>
      expect(sendConversationStream).toHaveBeenCalledWith(
        {
          text: "Explain Python context managers.",
          conversation_id: undefined,
          agent_id: globalAgent.id,
          model_override: null,
          attachment_ids: [],
        },
        expect.any(Object),
      ),
    );
    expect(await screen.findByText(assistantMessage.content)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: globalAgent.name })).not.toBeInTheDocument();
    expect(screen.getByText(globalAgent.name)).toBeInTheDocument();
    expect(navigationMocks.replace).toHaveBeenCalledWith(
      "/chat?session=conversation-new",
      { scroll: false },
    );
    expect(historyChanged).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText(/message auto reign/i)).toBeDisabled();

    fireEvent.submit(screen.getByRole("form", { name: /chat composer/i }));
    expect(sendConversationStream).toHaveBeenCalledTimes(1);

    vi.mocked(getConversation).mockResolvedValueOnce({
      ...existingConversation,
      id: "conversation-new",
      href: "/chat?session=conversation-new",
    });
    view.rerender(<ChatWorkspace sessionId="conversation-new" />);
    await waitFor(() =>
      expect(screen.getByLabelText(/message auto reign/i)).toBeEnabled(),
    );

    const toolbar = container.querySelector(".composer-toolbar");
    expect(toolbar).not.toBeNull();
    expect(toolbar?.querySelector(".composer-toolbar__left")).not.toBeNull();
    expect(toolbar?.querySelector(".composer-toolbar__right")).not.toBeNull();
    window.removeEventListener("auto-reign:conversations-changed", historyChanged);
  });

  it("uploads drafts, removes one, and sends a snapshot of the remaining ids", async () => {
    vi.mocked(uploadAttachment)
      .mockResolvedValueOnce(attachment("attachment-1", "one.txt"))
      .mockResolvedValueOnce(attachment("attachment-2", "two.png"));
    renderWorkspace();
    await screen.findByRole("button", { name: globalAgent.name });

    fireEvent.change(screen.getByLabelText(/^attach files$/i), {
      target: {
        files: [
          new File(["one"], "one.txt", { type: "text/plain" }),
          new File(["png"], "two.png", { type: "image/png" }),
        ],
      },
    });
    expect(await screen.findByText("one.txt")).toBeInTheDocument();
    expect(await screen.findByText("two.png")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /remove one.txt/i }));
    await waitFor(() => expect(screen.queryByText("one.txt")).not.toBeInTheDocument());

    await enterMessage("Use the diagram");
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() =>
      expect(sendConversationStream).toHaveBeenCalledWith(
        expect.objectContaining({ attachment_ids: ["attachment-2"] }),
        expect.any(Object),
      ),
    );
  });

  it("restores committed unbound drafts after a page refresh", async () => {
    vi.mocked(listAttachmentDrafts).mockResolvedValue([
      attachment("attachment-recovered", "recovered.pdf"),
    ]);

    renderWorkspace();

    expect(await screen.findByText("recovered.pdf")).toBeInTheDocument();
    expect(listAttachmentDrafts).toHaveBeenCalledTimes(1);
  });

  it("blocks send during recovery and retries a failed draft listing", async () => {
    const firstRecovery = deferred<Attachment[]>();
    vi.mocked(listAttachmentDrafts)
      .mockReturnValueOnce(firstRecovery.promise)
      .mockResolvedValueOnce([attachment("attachment-retried", "retried.txt")]);
    renderWorkspace();
    await screen.findByRole("button", { name: globalAgent.name });
    await enterMessage("Wait for recovery");

    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
    firstRecovery.reject(new Error("Object store unavailable"));
    expect(await screen.findByText("Draft attachments could not be loaded.")).toHaveAttribute(
      "role",
      "alert",
    );
    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /retry loading attachments/i }));

    expect(await screen.findByText("retried.txt")).toBeInTheDocument();
    expect(listAttachmentDrafts).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: /send message/i })).toBeEnabled();
  });

  it("ignores a stale draft recovery when the session changes", async () => {
    const staleDrafts = deferred<Attachment[]>();
    vi.mocked(listAttachmentDrafts)
      .mockReturnValueOnce(staleDrafts.promise)
      .mockResolvedValueOnce([attachment("attachment-current", "current.txt")]);
    vi.mocked(getConversation).mockImplementation(async (id) => ({
      ...existingConversation,
      id,
      href: `/chat?session=${id}`,
    }));
    const view = renderWorkspace(<ChatWorkspace sessionId="conversation-old" />);

    view.rerender(<ChatWorkspace sessionId="conversation-current" />);
    expect(await screen.findByText("current.txt")).toBeInTheDocument();
    staleDrafts.resolve([attachment("attachment-stale", "stale.txt")]);
    await staleDrafts.promise;

    await waitFor(() => expect(screen.queryByText("stale.txt")).not.toBeInTheDocument());
    expect(screen.getByText("current.txt")).toBeInTheDocument();
  });

  it("clears exactly the accepted draft ids before a later provider failure", async () => {
    vi.mocked(listAttachmentDrafts).mockResolvedValue([
      attachment("attachment-1", "one.txt"),
      attachment("attachment-2", "two.txt"),
    ]);
    vi.mocked(sendConversationStream).mockImplementation(async (_payload, callbacks) => {
      callbacks.onAccepted?.({
        conversation_id: "conversation-accepted",
        user_message_id: "message-user-accepted",
        assistant_message_id: "message-assistant-failed",
        attachment_ids: ["attachment-1"],
      });
      throw new Error("Provider unavailable");
    });
    renderWorkspace();
    await screen.findByText("one.txt");
    await enterMessage("Use one attachment");

    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() => {
      const draftList = screen.getByRole("list", { name: /draft attachments/i });
      expect(within(draftList).queryByText("one.txt")).not.toBeInTheDocument();
      expect(within(draftList).getByText("two.txt")).toBeInTheDocument();
    });
    expect(
      screen.getByRole("button", { name: /preview one.txt/i }).closest("article"),
    ).toHaveAttribute("data-message-id", "message-user-accepted");
  });

  it("keeps drafts when sending fails before accepted", async () => {
    vi.mocked(listAttachmentDrafts).mockResolvedValue([
      attachment("attachment-1", "source.txt"),
    ]);
    vi.mocked(sendConversationStream).mockRejectedValue(new Error("Commit failed"));
    renderWorkspace();
    await screen.findByText("source.txt");
    await enterMessage("Keep this draft");

    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    const draftList = await screen.findByRole("list", { name: /draft attachments/i });
    expect(within(draftList).getByText("source.txt")).toBeInTheDocument();
  });

  it("disables sending while an attachment mutation is pending", async () => {
    const upload = deferred<Attachment>();
    vi.mocked(uploadAttachment).mockReturnValue(upload.promise);
    renderWorkspace();
    await screen.findByRole("button", { name: globalAgent.name });
    await enterMessage("Wait for upload");

    fireEvent.change(screen.getByLabelText(/^attach files$/i), {
      target: { files: [new File(["abc"], "pending.txt", { type: "text/plain" })] },
    });

    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
    upload.resolve(attachment("attachment-pending", "pending.txt"));
    expect(await screen.findByText("pending.txt")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /send message/i })).toBeEnabled(),
    );
  });

  it("keeps composer actions ordered and grouped at narrow widths", async () => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    renderWorkspace();
    const toolbar = await screen.findByRole("toolbar", { name: /message actions/i });
    const attachmentButton = within(toolbar).getByRole("button", { name: /^attach files$/i });
    const agentButton = within(toolbar).getByRole("button", { name: globalAgent.name });
    const modelButton = within(toolbar).getByRole("button", { name: /select model/i });
    const sendButton = within(toolbar).getByRole("button", { name: /send message/i });

    expect(attachmentButton.compareDocumentPosition(agentButton)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(agentButton.compareDocumentPosition(modelButton)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(modelButton.compareDocumentPosition(sendButton)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(attachmentButton.closest("[data-composer-group]")).toHaveAttribute(
      "data-composer-group",
      "left",
    );
    expect(sendButton.closest("[data-composer-group]")).toHaveAttribute(
      "data-composer-group",
      "right",
    );
    expect(toolbar).toHaveClass("composer-toolbar--wrap-safe");
  });

  it("includes a selected model override only when creating a conversation", async () => {
    renderWorkspace();
    await screen.findByRole("button", { name: globalAgent.name });
    await chooseModel("custom-chat");
    await enterMessage("Use the local model.");

    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() =>
      expect(sendConversationStream).toHaveBeenCalledWith(
        {
          text: "Use the local model.",
          conversation_id: undefined,
          agent_id: globalAgent.id,
          model_override: { provider: "local-runtime", model: "custom-chat" },
          attachment_ids: [],
        },
        expect.any(Object),
      ),
    );
  });

  it("loads an existing conversation with a read-only agent and persists model changes", async () => {
    renderWorkspace(<ChatWorkspace sessionId={existingConversation.id} />);

    expect(await screen.findByText(userMessage.content)).toBeInTheDocument();
    expect(screen.getByText(globalAgent.name)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: globalAgent.name })).not.toBeInTheDocument();
    await chooseModel("qwen3.7-max");

    await waitFor(() =>
      expect(setConversationModel).toHaveBeenCalledWith(existingConversation.id, {
        provider: "qwen",
        model: "qwen3.7-max",
      }),
    );
    expect(sendConversationStream).not.toHaveBeenCalled();
  });

  it("sends only the conversation id for an existing conversation", async () => {
    renderWorkspace(<ChatWorkspace sessionId={existingConversation.id} />);
    await screen.findByText(userMessage.content);
    await enterMessage("Continue this conversation.");

    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() =>
      expect(sendConversationStream).toHaveBeenCalledWith(
        {
          text: "Continue this conversation.",
          conversation_id: existingConversation.id,
          agent_id: undefined,
          model_override: undefined,
          attachment_ids: [],
        },
        expect.any(Object),
      ),
    );
  });

  it("keeps existing history readable when auxiliary Agent and model lists fail", async () => {
    vi.mocked(listAgents).mockRejectedValue(new Error("Agent list unavailable"));
    vi.mocked(getModels).mockRejectedValue(new Error("Model list unavailable"));

    renderWorkspace(<ChatWorkspace sessionId={existingConversation.id} />);

    expect(await screen.findByText(userMessage.content)).toBeInTheDocument();
    expect(screen.getByText(assistantMessage.content)).toBeInTheDocument();
    expect(screen.getByText(globalAgent.name)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Agent or model options could not be refreshed",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("errors.options_load");
  });

  it("clears an existing model override with explicit null", async () => {
    vi.mocked(getConversation).mockResolvedValue({
      ...existingConversation,
      model_override: { provider: "qwen", model: "qwen3.7-max" },
    });
    renderWorkspace(<ChatWorkspace sessionId={existingConversation.id} />);
    await screen.findByText(userMessage.content);

    await chooseModel(/follow agent default/i);

    await waitFor(() =>
      expect(setConversationModel).toHaveBeenCalledWith(existingConversation.id, null),
    );
  });

  it("uses synchronous guards to prevent duplicate sends and model updates", async () => {
    const sendRequest = deferred<{ conversation_id: string; message: ConversationMessage }>();
    vi.mocked(sendConversationStream).mockReturnValue(sendRequest.promise);
    const { container, unmount } = renderWorkspace();
    await screen.findByRole("button", { name: globalAgent.name });
    await enterMessage("Only once");
    const form = screen.getByRole("form", { name: /chat composer/i });

    fireEvent.submit(form);
    fireEvent.submit(form);

    expect(sendConversationStream).toHaveBeenCalledTimes(1);
    unmount();

    const modelRequest = deferred<ConversationDetailResponse>();
    vi.mocked(setConversationModel).mockReturnValue(modelRequest.promise);
    renderWorkspace(<ChatWorkspace sessionId={existingConversation.id} />);
    await screen.findByText(userMessage.content);
    fireEvent.click(screen.getByRole("button", { name: /select model/i }));
    const option = await screen.findByRole("option", { name: "qwen3.7-max" });
    fireEvent.click(option);
    fireEvent.click(option);

    expect(setConversationModel).toHaveBeenCalledTimes(1);
    expect(container).toBeDefined();
  });

  it("keeps user input and partial assistant output when streaming fails", async () => {
    vi.mocked(sendConversationStream).mockImplementation(async (_payload, callbacks) => {
      callbacks.onDelta("Partial answer");
      throw new Error("Provider unavailable");
    });
    const historyChanged = vi.fn();
    window.addEventListener("auto-reign:conversations-changed", historyChanged);
    renderWorkspace();
    await screen.findByRole("button", { name: globalAgent.name });
    await enterMessage("Keep my source text");

    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    expect(await screen.findByText("Keep my source text")).toBeInTheDocument();
    expect(await screen.findByText("Partial answer")).toBeInTheDocument();
    expect(screen.getByText(/response failed/i)).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Provider unavailable");
    expect(historyChanged).toHaveBeenCalledTimes(1);
    window.removeEventListener("auto-reign:conversations-changed", historyChanged);
  });

  it("routes a prepared first turn failure to its persisted conversation and locks mutations", async () => {
    vi.mocked(sendConversationStream).mockImplementation(async (_payload, callbacks) => {
      callbacks.onDelta("Persisted partial");
      throw new ApiError("The model request failed.", {
        assistantMessageId: "assistant-failed",
        code: "provider_call_failed",
        conversationId: "conversation-failed",
        status: 502,
      });
    });
    renderWorkspace();
    await screen.findByRole("button", { name: globalAgent.name });
    await enterMessage("Keep this in one conversation");

    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    await waitFor(() =>
      expect(navigationMocks.replace).toHaveBeenCalledWith(
        "/chat?session=conversation-failed",
        { scroll: false },
      ),
    );
    expect(screen.getByText("Persisted partial")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: globalAgent.name })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/message auto reign/i)).toBeDisabled();
    fireEvent.submit(screen.getByRole("form", { name: /chat composer/i }));
    expect(sendConversationStream).toHaveBeenCalledTimes(1);
  });

  it("removes an unavailable Agent and selects the next visible Agent", async () => {
    vi.mocked(sendConversationStream).mockRejectedValue(
      new ApiError("Agent is unavailable.", {
        code: "agent_unavailable",
        status: 409,
      }),
    );
    renderWorkspace();
    await screen.findByRole("button", { name: globalAgent.name });
    await enterMessage("Try the selected Agent");

    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    expect(await screen.findByRole("button", { name: privateAgent.name })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: globalAgent.name })).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Agent is unavailable");
  });

  it("renders stored failed partials and disables an unavailable conversation", async () => {
    vi.mocked(getConversation).mockResolvedValue({
      ...existingConversation,
      agent: { ...existingConversation.agent, is_available: false },
      messages: [
        userMessage,
        {
          ...assistantMessage,
          id: "message-failed",
          status: "failed",
          content: "Stored partial answer",
        },
      ],
    });
    const { container } = renderWorkspace(<ChatWorkspace sessionId={existingConversation.id} />);

    expect(await screen.findByText("Stored partial answer")).toBeInTheDocument();
    expect(screen.getByText(/response failed/i)).toBeInTheDocument();
    expect(screen.getByText(/agent is unavailable/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/message auto reign/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /select model/i })).toBeDisabled();
    expect(container.querySelector(".message-failed")).not.toBeNull();
    expect(container.querySelector(".agent-unavailable")).not.toBeNull();
  });

  it("disables the entire mutation surface for a generating conversation", async () => {
    vi.mocked(getConversation).mockResolvedValue({
      ...existingConversation,
      status: "generating",
    });
    renderWorkspace(<ChatWorkspace sessionId={existingConversation.id} />);

    expect(await screen.findByText(/generation in progress/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/message auto reign/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /select model/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /send message/i })).toBeDisabled();
  });

  it("fails closed when a model update reports that the Agent was deleted", async () => {
    vi.mocked(setConversationModel).mockRejectedValue(
      new ApiError("Agent is unavailable.", {
        code: "agent_unavailable",
        status: 409,
      }),
    );
    renderWorkspace(<ChatWorkspace sessionId={existingConversation.id} />);
    await screen.findByText(userMessage.content);

    await chooseModel("qwen3.7-max");

    expect(await screen.findByRole("status")).toHaveTextContent(/agent is unavailable/i);
    expect(screen.getByLabelText(/message auto reign/i)).toBeDisabled();
    expect(screen.getByRole("button", { name: /select model/i })).toBeDisabled();
  });

  it("localizes an unknown model API error without exposing its backend message or i18n key", async () => {
    i18next.changeLanguage("zh-CN");
    vi.mocked(setConversationModel).mockRejectedValue(
      new ApiError("Internal provider topology leaked from backend.", {
        code: "unexpected_backend_failure",
        status: 500,
      }),
    );
    renderWorkspace(<ChatWorkspace sessionId={existingConversation.id} />);
    await screen.findByText(userMessage.content);

    fireEvent.click(screen.getByRole("button", { name: /选择模型/i }));
    fireEvent.click(await screen.findByRole("option", { name: "qwen3.7-max" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("无法切换当前会话的模型");
    expect(screen.getByRole("alert")).not.toHaveTextContent("Internal provider topology");
    expect(screen.getByRole("alert")).not.toHaveTextContent("errors.model_update");
  });

  it("ignores stale detail loads when the session changes", async () => {
    const oldRequest = deferred<ConversationDetailResponse>();
    const newerConversation = {
      ...existingConversation,
      id: "conversation-newer",
      href: "/chat?session=conversation-newer",
      title: "Newer conversation",
      messages: [{ ...userMessage, id: "newer-user", content: "Newer content" }],
    };
    vi.mocked(getConversation).mockImplementation((id) =>
      id === "conversation-old" ? oldRequest.promise : Promise.resolve(newerConversation),
    );
    const view = renderWorkspace(<ChatWorkspace sessionId="conversation-old" />);

    view.rerender(<ChatWorkspace sessionId="conversation-newer" />);
    expect(await screen.findByText("Newer content")).toBeInTheDocument();
    oldRequest.resolve({
      ...existingConversation,
      id: "conversation-old",
      messages: [{ ...userMessage, id: "old-user", content: "Stale content" }],
    });

    await waitFor(() => expect(screen.queryByText("Stale content")).not.toBeInTheDocument());
  });

  it("does not route or write UI state after an in-flight send is unmounted", async () => {
    const request = deferred<{ conversation_id: string; message: ConversationMessage }>();
    vi.mocked(sendConversationStream).mockReturnValue(request.promise);
    const view = renderWorkspace();
    await screen.findByRole("button", { name: globalAgent.name });
    await enterMessage("Unmount me");
    fireEvent.click(screen.getByRole("button", { name: /send message/i }));

    view.unmount();
    request.resolve({ conversation_id: "late-conversation", message: assistantMessage });
    await request.promise;
    await Promise.resolve();

    expect(navigationMocks.replace).not.toHaveBeenCalled();
  });

  it("keeps Shift+Enter as a multiline edit without sending", async () => {
    renderWorkspace();
    const input = await enterMessage("first line\nsecond line");

    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });

    expect(sendConversationStream).not.toHaveBeenCalled();
    expect(input).toHaveValue("first line\nsecond line");
  });
});

const modelPickerLabels = {
  agentDefault: "Agent default: {{model}}",
  followAgentDefault: "Follow Agent default",
  listbox: "Available models",
  modelUnavailable: "Unavailable",
  noProviders: "No providers",
  selectModel: "Select model",
};

function renderModelPicker(
  overrides: Partial<ComponentProps<typeof ModelPicker>> = {},
) {
  const props: ComponentProps<typeof ModelPicker> = {
    agentDefault: { provider: "qwen", model: "qwen3.7-plus" },
    labels: modelPickerLabels,
    onOpenChange: vi.fn(),
    onSelect: vi.fn(),
    open: false,
    providers: [
      { provider: "qwen", models: ["qwen3.7-plus"] },
      { provider: "local-runtime", models: ["custom-chat"] },
    ],
    selected: null,
    ...overrides,
  };
  const view = render(<ModelPicker {...props} />);
  return { ...view, props };
}

describe("ModelPicker contract", () => {
  it("emits null and closes when following the agent default model", () => {
    const onOpenChange = vi.fn();
    const onSelect = vi.fn();
    renderModelPicker({
      onOpenChange,
      onSelect,
      open: true,
      selected: { provider: "qwen", model: "qwen3.7-plus" },
    });

    const followOption = screen.getByRole("option", { name: /follow agent default/i });
    expect(followOption).toHaveTextContent("qwen / qwen3.7-plus");
    fireEvent.click(followOption);

    expect(onSelect).toHaveBeenCalledWith(null);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("supports arbitrary provider names and emits a ModelRef", () => {
    const onSelect = vi.fn();
    renderModelPicker({ onSelect, open: true });

    fireEvent.click(screen.getByRole("option", { name: "custom-chat" }));

    expect(onSelect).toHaveBeenCalledWith({
      provider: "local-runtime",
      model: "custom-chat",
    } satisfies ModelRef);
  });

  it("always offers follow-default when no providers are configured", () => {
    renderModelPicker({ agentDefault: null, open: true, providers: [] });

    expect(screen.getByRole("option", { name: /follow agent default/i })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(screen.getByText("No providers")).toBeInTheDocument();
  });

  it("keeps every option fail-closed when a controlled open picker is disabled", () => {
    const onOpenChange = vi.fn();
    const onSelect = vi.fn();
    renderModelPicker({ disabled: true, onOpenChange, onSelect, open: true });

    const listbox = screen.getByRole("listbox", { name: /available models/i });
    expect(within(listbox).getByRole("option", { name: /follow agent default/i })).toBeDisabled();
    expect(within(listbox).getByRole("option", { name: "custom-chat" })).toBeDisabled();
    fireEvent.click(screen.getByRole("option", { name: "custom-chat" }));
    expect(onSelect).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
