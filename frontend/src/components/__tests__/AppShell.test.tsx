import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "../AppShell";
import i18next from "@/i18n/setup";
import { deleteConversation, getCurrentUser, listConversations, renameConversation } from "@/lib/api";
import { clearAuthToken } from "@/lib/auth";
import type { ConversationHistoryItem, User } from "@/lib/types";

const navigationMocks = vi.hoisted(() => ({
  pathname: "/chat",
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigationMocks.pathname,
  useRouter: () => ({ replace: navigationMocks.replace }),
}));

vi.mock("@/lib/api", () => ({
  deleteConversation: vi.fn(),
  getCurrentUser: vi.fn(),
  listConversations: vi.fn(),
  renameConversation: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({ clearAuthToken: vi.fn() }));

const firstConversation: ConversationHistoryItem = {
  id: "conversation-one",
  title: "Python context managers",
  href: "/chat?session=conversation-one",
  agent: { id: "agent-one", name: "Python coach", is_available: true },
  model_override: null,
  status: "idle",
  started_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:10:00Z",
  last_message: "Latest answer",
};

const secondConversation: ConversationHistoryItem = {
  ...firstConversation,
  id: "conversation-two",
  title: "Redis patterns",
  href: "/chat?session=conversation-two",
};

const userFixture: User = {
  id: 7,
  username: "alice",
  display_name: "Alice",
  role: "user",
  is_active: true,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

function clickMenuItem(name: RegExp) {
  const menuItem = screen.getByRole("menuitem", { name });
  fireEvent.pointerDown(menuItem);
  fireEvent.click(menuItem);
}

describe("AppShell", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    navigationMocks.pathname = "/chat";
    window.history.replaceState(null, "", "/chat");
    i18next.changeLanguage("en");
    window.localStorage.clear();
    document.documentElement.dataset.theme = "light";
    vi.mocked(getCurrentUser).mockResolvedValue(userFixture);
    vi.mocked(listConversations).mockResolvedValue({
      conversations: [firstConversation, secondConversation],
    });
    vi.mocked(renameConversation).mockImplementation(async (id, title) => ({
      ...(id === firstConversation.id ? firstConversation : secondConversation),
      title,
    }));
    vi.mocked(deleteConversation).mockResolvedValue({
      id: secondConversation.id,
      status: "deleted",
    });
  });

  it("renders setup without the authenticated shell", () => {
    navigationMocks.pathname = "/setup";
    render(<AppShell><div>Administrator setup</div></AppShell>);

    expect(screen.getByText("Administrator setup")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /new chat/i })).not.toBeInTheDocument();
    expect(listConversations).not.toHaveBeenCalled();
    expect(getCurrentUser).not.toHaveBeenCalled();
  });

  it("shows one new chat and the complete personal management navigation", async () => {
    render(<AppShell><div>Current page</div></AppShell>);

    expect(screen.getAllByRole("link", { name: /^new chat$/i })).toHaveLength(1);
    expect(screen.getByRole("link", { name: /^agents$/i })).toHaveAttribute(
      "href",
      "/agents",
    );
    expect(screen.getByRole("link", { name: /^agent workspaces$/i })).toHaveAttribute(
      "href",
      "/workspaces",
    );
    expect(screen.getByRole("link", { name: /^knowledge bases$/i })).toHaveAttribute(
      "href",
      "/knowledge",
    );
    expect(await screen.findByRole("button", { name: /^alice$/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /global agents/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /global workspaces/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /global knowledge/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /user management/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/new interview|new learning/i)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /history/i })).toBeInTheDocument();
  });

  it("adds global resource and user management only after /me returns admin", async () => {
    vi.mocked(getCurrentUser).mockResolvedValue({ ...userFixture, role: "admin" });
    render(<AppShell><div>Current page</div></AppShell>);

    expect(await screen.findByRole("link", { name: /global agents/i })).toHaveAttribute(
      "href",
      "/admin/agents",
    );
    expect(screen.getByRole("link", { name: /global workspaces/i })).toHaveAttribute(
      "href",
      "/admin/workspaces",
    );
    expect(screen.getByRole("link", { name: /global knowledge/i })).toHaveAttribute(
      "href",
      "/admin/knowledge",
    );
    expect(screen.getByRole("link", { name: /user management/i })).toHaveAttribute(
      "href",
      "/admin/users",
    );
  });

  it("uses the backend-provided unified chat href for every history item", async () => {
    render(<AppShell><div>Current page</div></AppShell>);

    expect(await screen.findByRole("link", { name: firstConversation.title })).toHaveAttribute(
      "href",
      firstConversation.href,
    );
    expect(screen.getByRole("link", { name: secondConversation.title })).toHaveAttribute(
      "href",
      secondConversation.href,
    );
  });

  it("marks the single workspace navigation entry active for detail routes", async () => {
    navigationMocks.pathname = "/workspaces/ws-1";
    render(<AppShell><div>Workspace page</div></AppShell>);

    const links = screen.getAllByRole("link", { name: /^agent workspaces$/i });
    expect(links).toHaveLength(1);
    expect(links[0]).toHaveAttribute("data-active", "true");
  });

  it("marks the knowledge navigation entry active for detail routes", async () => {
    navigationMocks.pathname = "/knowledge/collection-1";
    render(<AppShell><div>Knowledge page</div></AppShell>);

    expect(screen.getByRole("link", { name: /^knowledge bases$/i })).toHaveAttribute(
      "data-active",
      "true",
    );
  });

  it("refreshes unified history after a conversation change event", async () => {
    vi.mocked(listConversations)
      .mockResolvedValueOnce({ conversations: [firstConversation] })
      .mockResolvedValueOnce({ conversations: [secondConversation] });
    render(<AppShell><div>Current page</div></AppShell>);
    expect(await screen.findByRole("link", { name: firstConversation.title })).toBeInTheDocument();

    window.dispatchEvent(new Event("auto-reign:conversations-changed"));

    expect(await screen.findByRole("link", { name: secondConversation.title })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: firstConversation.title })).not.toBeInTheDocument();
    expect(listConversations).toHaveBeenCalledTimes(2);
  });

  it("never lets a delayed conversation request cross an authentication boundary", async () => {
    let resolveAlice!: (value: { conversations: ConversationHistoryItem[] }) => void;
    let resolveBob!: (value: { conversations: ConversationHistoryItem[] }) => void;
    const aliceRequest = new Promise<{ conversations: ConversationHistoryItem[] }>((resolve) => {
      resolveAlice = resolve;
    });
    const bobRequest = new Promise<{ conversations: ConversationHistoryItem[] }>((resolve) => {
      resolveBob = resolve;
    });
    vi.mocked(listConversations)
      .mockReturnValueOnce(aliceRequest)
      .mockReturnValueOnce(bobRequest);
    const view = render(<AppShell><div>Current page</div></AppShell>);

    navigationMocks.pathname = "/login";
    view.rerender(<AppShell><div>Login page</div></AppShell>);
    await act(async () => {
      resolveAlice({ conversations: [firstConversation] });
      await aliceRequest;
    });
    navigationMocks.pathname = "/chat";
    view.rerender(<AppShell><div>Bob chat</div></AppShell>);

    expect(screen.queryByRole("link", { name: firstConversation.title })).not.toBeInTheDocument();

    resolveBob({ conversations: [secondConversation] });
    expect(await screen.findByRole("link", { name: secondConversation.title })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: firstConversation.title })).not.toBeInTheDocument();
  });

  it("renames a conversation by id and preserves the backend href", async () => {
    render(<AppShell><div>Current page</div></AppShell>);
    fireEvent.click(await screen.findByRole("button", {
      name: `Actions for ${firstConversation.title}`,
    }));
    clickMenuItem(/rename/i);
    fireEvent.change(screen.getByLabelText(/conversation name/i), {
      target: { value: "Renamed conversation" },
    });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(renameConversation).toHaveBeenCalledWith(firstConversation.id, "Renamed conversation"),
    );
    expect(await screen.findByRole("link", { name: "Renamed conversation" })).toHaveAttribute(
      "href",
      firstConversation.href,
    );
  });

  it("deletes a non-current conversation without routing", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(listConversations)
      .mockResolvedValueOnce({ conversations: [firstConversation, secondConversation] })
      .mockResolvedValueOnce({ conversations: [firstConversation] });
    render(<AppShell><div>Current page</div></AppShell>);
    fireEvent.click(await screen.findByRole("button", {
      name: `Actions for ${secondConversation.title}`,
    }));
    clickMenuItem(/delete/i);

    await waitFor(() => expect(deleteConversation).toHaveBeenCalledWith(secondConversation.id));
    expect(navigationMocks.replace).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: secondConversation.title })).not.toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("always routes to new chat after deleting the current conversation", async () => {
    navigationMocks.pathname = "/chat";
    window.history.replaceState(
      null,
      "",
      `/chat?mode=compact&session=${firstConversation.id}&panel=history`,
    );
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(deleteConversation).mockResolvedValue({ id: firstConversation.id, status: "deleted" });
    vi.mocked(listConversations)
      .mockResolvedValueOnce({ conversations: [firstConversation, secondConversation] })
      .mockResolvedValueOnce({ conversations: [secondConversation] });
    render(<AppShell><div>Current page</div></AppShell>);
    fireEvent.click(await screen.findByRole("button", {
      name: `Actions for ${firstConversation.title}`,
    }));
    clickMenuItem(/delete/i);

    await waitFor(() => expect(deleteConversation).toHaveBeenCalledWith(firstConversation.id));
    expect(navigationMocks.replace).toHaveBeenCalledWith("/chat");
    confirmSpy.mockRestore();
  });

  it("preserves account, language, theme, logout, and collapse controls", async () => {
    render(<AppShell><div>Current page</div></AppShell>);
    const shell = screen.getByText("Current page").closest(".app-shell");
    fireEvent.click(screen.getByRole("button", { name: /collapse sidebar/i }));
    expect(shell).toHaveAttribute("data-sidebar-collapsed", "true");

    fireEvent.click(await screen.findByRole("button", { name: /^alice$/i }));
    fireEvent.click(screen.getByRole("button", { name: /简体中文/i }));
    expect(await screen.findByRole("button", { name: /english/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "智能体" })).toHaveAttribute(
      "href",
      "/agents",
    );
    expect(screen.getByRole("link", { name: "智能体工作区" })).toHaveAttribute(
      "href",
      "/workspaces",
    );
    expect(screen.getByRole("link", { name: "资料库" })).toHaveAttribute(
      "href",
      "/knowledge",
    );
    fireEvent.click(screen.getByRole("button", { name: /深色模式/i }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    fireEvent.click(screen.getByRole("button", { name: /退出登录/i }));
    expect(clearAuthToken).toHaveBeenCalledTimes(1);
    expect(navigationMocks.replace).toHaveBeenCalledWith("/login");
  });
});
