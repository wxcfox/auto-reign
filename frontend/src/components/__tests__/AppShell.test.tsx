import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "../AppShell";
import i18next from "@/i18n/setup";
import { deleteConversation, getCurrentUser, listConversations, renameConversation } from "@/lib/api";
import { clearAuthToken } from "@/lib/auth";

const navigationMocks = vi.hoisted(() => ({
  pathname: "/interview",
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => navigationMocks.pathname,
  useRouter: () => ({
    replace: navigationMocks.replace,
  }),
}));

vi.mock("@/lib/api", () => ({
  deleteConversation: vi.fn(),
  getCurrentUser: vi.fn(),
  listConversations: vi.fn(),
  renameConversation: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
  clearAuthToken: vi.fn(),
}));

describe("AppShell", () => {
  function conversationResponse(label: string, kind: "chat" | "interview" | "learning", id: string) {
    const href = kind === "chat"
      ? `/chat?session=${id}`
      : kind === "interview"
        ? `/interview?session=${id}`
        : `/learn?session=${id}`;
    return {
      conversations: [
        {
          id,
          kind,
          title: label,
          href,
          started_at: "2026-06-23T00:00:00Z",
          updated_at: "2026-06-23T00:10:00Z",
          last_message: `${label} latest message`,
        },
      ],
    };
  }

  function clickMenuItem(name: RegExp) {
    const menuItem = screen.getByRole("menuitem", { name });
    fireEvent.pointerDown(menuItem);
    fireEvent.click(menuItem);
  }

  beforeEach(() => {
    vi.resetAllMocks();
    navigationMocks.pathname = "/interview";
    window.history.replaceState(null, "", "/");
    i18next.changeLanguage("en");
    try {
      window.localStorage?.clear();
    } catch {
      // Tests run without persistent localStorage.
    }
    document.documentElement.dataset.theme = "light";
    vi.mocked(deleteConversation).mockResolvedValue({ id: "deleted-session", status: "deleted" });
    vi.mocked(renameConversation).mockImplementation((id, title) =>
      Promise.resolve({
        id,
        kind: "interview",
        title,
        href: `/interview?session=${id}`,
        started_at: "2026-06-23T00:00:00Z",
        updated_at: "2026-06-23T00:10:00Z",
        last_message: "Renamed latest message",
      }),
    );
    vi.mocked(getCurrentUser).mockResolvedValue({
      id: 7,
      username: "alice",
      display_name: "Alice",
      is_active: true,
      created_at: "2026-07-07T00:00:00Z",
      updated_at: "2026-07-07T00:00:00Z",
    });
    vi.mocked(listConversations).mockResolvedValue({
      conversations: [
        ...conversationResponse("Python context managers", "chat", "chat-session").conversations,
        ...conversationResponse("Active backend interview", "interview", "active-session").conversations,
        ...conversationResponse("Redis cache learning", "learning", "learning-session").conversations,
      ],
    });
  });

  it("renders new chat first, followed by interview and learning entries", async () => {
    render(
      <AppShell>
        <div>Current page</div>
      </AppShell>,
    );

    const newChat = screen.getByRole("link", { name: /^New chat$/i });
    const newInterview = screen.getByRole("link", { name: /New interview/i });
    const newLearning = screen.getByRole("link", { name: /New learning/i });
    expect(newChat).toHaveAttribute("href", "/chat");
    expect(newChat.compareDocumentPosition(newInterview) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    expect(newInterview.compareDocumentPosition(newLearning) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    expect(screen.getByRole("navigation", { name: /Primary/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Interview$/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/Recent/i)).not.toBeInTheDocument();

    const activeSessionLink = await screen.findByRole("link", { name: /Active backend interview/i });
    expect(activeSessionLink).toHaveAttribute("href", "/interview?session=active-session");
    expect(activeSessionLink).toHaveAttribute("title", "Active backend interview");
    expect(screen.getByRole("link", { name: /Redis cache learning/i }))
      .toHaveAttribute("href", "/learn?session=learning-session");
    expect(screen.getByRole("link", { name: /Python context managers/i }))
      .toHaveAttribute("href", "/chat?session=chat-session");
    expect(screen.queryByText(/Completed/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Working/i)).not.toBeInTheDocument();

    const libraryLink = screen.getByRole("link", { name: /Library/i });
    const moreButton = screen.getByRole("button", { name: /More/i });
    expect(moreButton).toHaveAttribute("aria-expanded", "false");
    expect(libraryLink.compareDocumentPosition(moreButton) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    const historyHeading = screen.getByRole("heading", { name: /History/i });
    expect(moreButton.compareDocumentPosition(historyHeading) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    const moreSection = moreButton.closest(".sidebar-more");
    expect(moreSection).not.toBeNull();
    expect(within(moreSection as HTMLElement).queryByRole("heading", { name: /History/i }))
      .not.toBeInTheDocument();
    fireEvent.click(moreButton);
    expect(moreButton).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: /Workbench/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Review/i })).not.toBeInTheDocument();

    const userButton = await screen.findByRole("button", { name: /^alice$/i });
    expect(userButton.querySelector(".lucide-chevron-up")).toBeInTheDocument();
    fireEvent.click(userButton);
    expect(userButton.querySelector(".lucide-chevron-down")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: /简体中文/i })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Dark mode/i })).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("shows the current username in the sidebar footer", async () => {
    render(
      <AppShell>
        <div>Current page</div>
      </AppShell>,
    );

    const userButton = await screen.findByRole("button", { name: /^alice$/i });

    expect(userButton).toHaveTextContent("alice");
    expect(userButton).not.toHaveTextContent("User #7");
    expect(getCurrentUser).toHaveBeenCalledTimes(1);
  });

  it("uses target-state buttons for language and theme settings", async () => {
    render(
      <AppShell>
        <div>Current page</div>
      </AppShell>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /^alice$/i }));
    const languageButton = await screen.findByRole("button", { name: /简体中文/i });
    fireEvent.click(languageButton);

    expect(await screen.findByRole("button", { name: /English/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^alice$/i })).toBeInTheDocument();

    const themeButton = screen.getByRole("button", { name: /深色模式/i });
    fireEvent.click(themeButton);

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(screen.getByRole("button", { name: /浅色模式/i })).toBeInTheDocument();
  });

  it("logs out from the user settings menu", async () => {
    render(
      <AppShell>
        <div>Current page</div>
      </AppShell>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /^alice$/i }));
    fireEvent.click(await screen.findByRole("button", { name: /Log out/i }));

    expect(clearAuthToken).toHaveBeenCalled();
    expect(navigationMocks.replace).toHaveBeenCalledWith("/login");
  });

  it("collapses and expands the sidebar", async () => {
    render(
      <AppShell>
        <div>Current page</div>
      </AppShell>,
    );

    const shell = screen.getByText("Current page").closest(".app-shell");
    expect(shell).toHaveAttribute("data-sidebar-collapsed", "false");

    fireEvent.click(screen.getByRole("button", { name: /Collapse sidebar/i }));

    expect(shell).toHaveAttribute("data-sidebar-collapsed", "true");
    expect(screen.getByRole("button", { name: /Expand sidebar/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Expand sidebar/i }));

    expect(shell).toHaveAttribute("data-sidebar-collapsed", "false");
  });

  it("refreshes sidebar history when interview sessions change", async () => {
    vi.mocked(listConversations)
      .mockResolvedValueOnce(conversationResponse("Initial backend interview", "interview", "initial-session"))
      .mockResolvedValueOnce(conversationResponse("Refreshed learning", "learning", "done-session"));

    render(
      <AppShell>
        <div>Current page</div>
      </AppShell>,
    );

    expect(await screen.findByRole("link", { name: /Initial backend interview/i }))
      .toHaveAttribute("href", "/interview?session=initial-session");

    window.dispatchEvent(new Event("auto-reign:conversations-changed"));

    expect(await screen.findByRole("link", { name: /Refreshed learning/i }))
      .toHaveAttribute("href", "/learn?session=done-session");
    expect(screen.queryByRole("link", { name: /Initial backend interview/i })).not.toBeInTheDocument();
    expect(listConversations).toHaveBeenCalledTimes(2);
  });

  it("renames a history conversation from the three-dot menu", async () => {
    vi.mocked(listConversations)
      .mockResolvedValueOnce(conversationResponse("Active backend interview", "interview", "active-session"));

    render(
      <AppShell>
        <div>Current page</div>
      </AppShell>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /Actions for Active backend interview/i }));
    clickMenuItem(/Rename/i);
    const input = screen.getByLabelText(/Conversation name/i);

    expect(input).toHaveValue("Active backend interview");

    fireEvent.change(input, { target: { value: "Cache practice" } });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/i }));

    await waitFor(() =>
      expect(renameConversation).toHaveBeenCalledWith("active-session", "Cache practice"),
    );
    const renamedLink = await screen.findByRole("link", { name: /Cache practice/i });
    expect(renamedLink).toHaveAttribute("href", "/interview?session=active-session");
    expect(renamedLink).toHaveTextContent("Cache practice");
    expect(renamedLink).not.toHaveTextContent("Renamed latest message");
  });

  it("deletes a history conversation after confirmation", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(listConversations)
      .mockResolvedValueOnce({
        conversations: [
          ...conversationResponse("Active backend interview", "interview", "active-session").conversations,
          ...conversationResponse("Redis cache learning", "learning", "learning-session").conversations,
        ],
      })
      .mockResolvedValueOnce(conversationResponse("Active backend interview", "interview", "active-session"));
    vi.mocked(deleteConversation).mockResolvedValue({ id: "learning-session", status: "deleted" });

    render(
      <AppShell>
        <div>Current page</div>
      </AppShell>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /Actions for Redis cache learning/i }));
    clickMenuItem(/Delete/i);

    await waitFor(() => expect(deleteConversation).toHaveBeenCalledWith("learning-session"));
    expect(confirmSpy).toHaveBeenCalledWith('Delete conversation "Redis cache learning"?');
    expect(navigationMocks.replace).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", { name: /Redis cache learning/i })).not.toBeInTheDocument();

    confirmSpy.mockRestore();
  });

  it("navigates away after deleting the current history conversation", async () => {
    navigationMocks.pathname = "/learn";
    window.history.replaceState(null, "", "/learn?session=learning-session");
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(listConversations)
      .mockResolvedValueOnce({
        conversations: [
          ...conversationResponse("Active backend interview", "interview", "active-session").conversations,
          ...conversationResponse("Redis cache learning", "learning", "learning-session").conversations,
        ],
      })
      .mockResolvedValueOnce(conversationResponse("Active backend interview", "interview", "active-session"));
    vi.mocked(deleteConversation).mockResolvedValue({ id: "learning-session", status: "deleted" });

    render(
      <AppShell>
        <div>Current page</div>
      </AppShell>,
    );

    fireEvent.click(await screen.findByRole("button", { name: /Actions for Redis cache learning/i }));
    clickMenuItem(/Delete/i);

    await waitFor(() => expect(deleteConversation).toHaveBeenCalledWith("learning-session"));
    expect(navigationMocks.replace).toHaveBeenCalledWith("/learn");

    confirmSpy.mockRestore();
  });
});
