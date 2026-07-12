import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ChatWorkspace } from "../ChatWorkspace";
import i18next from "@/i18n/setup";
import { getConversation, getModels, sendChatMessageStream } from "@/lib/api";


const navigationMocks = vi.hoisted(() => ({ replace: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => navigationMocks,
}));


vi.mock("@/lib/api", () => ({
  getConversation: vi.fn(),
  getModels: vi.fn(),
  sendChatMessageStream: vi.fn(),
}));


const assistantMessage = {
  id: "assistant-1",
  role: "assistant" as const,
  message_type: "chat_message",
  content: "Context managers reliably release resources.",
  created_at: "2026-07-12T00:00:01Z",
  metadata: {},
};


describe("ChatWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    i18next.changeLanguage("en");
    vi.mocked(getModels).mockResolvedValue({
      providers: [{ provider: "qwen", models: ["qwen3.7-plus"] }],
      default: { provider: "qwen", model: "qwen3.7-plus" },
    });
    vi.mocked(getConversation).mockRejectedValue(new Error("not used"));
    vi.mocked(sendChatMessageStream).mockImplementation(async (_payload, callbacks) => {
      callbacks.onDelta(assistantMessage.content);
      return { conversation_id: "chat-1", message: assistantMessage };
    });
  });

  it("starts a direct model chat and renders the streamed response", async () => {
    render(<ChatWorkspace />);
    const input = await screen.findByLabelText(/Message Auto Reign/i);

    fireEvent.change(input, { target: { value: "Explain Python context managers." } });
    fireEvent.click(screen.getByRole("button", { name: /Send message/i }));

    await waitFor(() =>
      expect(screen.getByText(/Context managers reliably release resources/i)).toBeInTheDocument(),
    );
    expect(sendChatMessageStream).toHaveBeenCalledWith(
      {
        text: "Explain Python context managers.",
        conversation_id: undefined,
        language: "en",
        provider: "qwen",
        model: "qwen3.7-plus",
      },
      expect.any(Object),
    );
    expect(navigationMocks.replace).toHaveBeenCalledWith("/chat?session=chat-1", {
      scroll: false,
    });
  });

  it("loads and continues an existing chat conversation", async () => {
    vi.mocked(getConversation).mockResolvedValue({
      id: "chat-1",
      kind: "chat",
      title: "Context managers",
      href: "/chat?session=chat-1",
      started_at: "2026-07-12T00:00:00Z",
      updated_at: "2026-07-12T00:00:01Z",
      last_message: assistantMessage.content,
      messages: [
        {
          id: "user-1",
          role: "user",
          message_type: "chat_message",
          content: "Explain context managers.",
          created_at: "2026-07-12T00:00:00Z",
          metadata: {},
        },
        assistantMessage,
      ],
    });
    render(<ChatWorkspace sessionId="chat-1" />);

    expect(await screen.findByText("Explain context managers.")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Message Auto Reign/i), {
      target: { value: "Show an example." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send message/i }));

    await waitFor(() =>
      expect(sendChatMessageStream).toHaveBeenCalledWith(
        expect.objectContaining({ conversation_id: "chat-1", text: "Show an example." }),
        expect.any(Object),
      ),
    );
  });

  it("uses Shift+Enter for multiline input without sending", async () => {
    render(<ChatWorkspace />);
    const input = await screen.findByLabelText(/Message Auto Reign/i);
    fireEvent.change(input, { target: { value: "first line\nsecond line" } });

    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });

    expect(sendChatMessageStream).not.toHaveBeenCalled();
    expect(input).toHaveValue("first line\nsecond line");
  });
});
