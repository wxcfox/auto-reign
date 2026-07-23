import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "../AppShell";
import type { SocketContextValue } from "@/contexts/SocketContext";
import i18next from "@/i18n/setup";
import { deleteTask, getCurrentUser, listTasks, renameTask } from "@/lib/api";
import { clearAuthToken } from "@/lib/auth";
import type { SocketEventHandlers } from "@/lib/socket-types";
import type { TaskHistoryItemResponse, User } from "@/lib/types";

const navigation = vi.hoisted(() => ({ pathname: "/chat", replace: vi.fn() }));
const socket = vi.hoisted(() => ({ handlers: {} as SocketEventHandlers, cleanup: vi.fn() }));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useRouter: () => ({ replace: navigation.replace }),
}));
vi.mock("@/contexts/SocketContext", () => ({
  useSocket: (): SocketContextValue => ({
    connected: true,
    joinTask: vi.fn(),
    leaveTask: vi.fn(),
    sendChatMessage: vi.fn(),
    cancel: vi.fn(),
    retry: vi.fn(),
    registerHandlers: (handlers: SocketEventHandlers) => {
      socket.handlers = handlers;
      return socket.cleanup;
    },
    onReconnect: vi.fn(),
  }),
}));
vi.mock("@/lib/api", () => ({
  deleteTask: vi.fn(),
  getCurrentUser: vi.fn(),
  listTasks: vi.fn(),
  renameTask: vi.fn(),
}));
vi.mock("@/lib/auth", () => ({ clearAuthToken: vi.fn() }));

const timestamp = "2026-07-22T00:00:00Z";
const firstTask: TaskHistoryItemResponse = {
  id: 7,
  name: "Python Task",
  href: "/chat?task=7",
  agent: { id: "agent-1", name: "Coach", is_available: true },
  model_override: null,
  status: "COMPLETED",
  created_at: timestamp,
  updated_at: timestamp,
  last_message: "answer",
};
const runningTask: TaskHistoryItemResponse = {
  ...firstTask,
  id: 8,
  name: "Running Task",
  href: "/chat?task=8",
  status: "RUNNING",
};
const user: User = {
  id: 7,
  username: "alice",
  display_name: "Alice",
  role: "user",
  is_active: true,
  created_at: timestamp,
  updated_at: timestamp,
};

function clickMenuItem(name: RegExp) {
  const item = screen.getByRole("menuitem", { name });
  fireEvent.pointerDown(item);
  fireEvent.click(item);
}

describe("AppShell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    socket.handlers = {};
    navigation.pathname = "/chat";
    window.history.replaceState(null, "", "/chat");
    window.localStorage.clear();
    void i18next.changeLanguage("en");
    vi.mocked(getCurrentUser).mockResolvedValue(user);
    vi.mocked(listTasks).mockResolvedValue({ tasks: [firstTask, runningTask] });
    vi.mocked(renameTask).mockImplementation(async (id, name) => ({
      ...(id === 7 ? firstTask : runningTask),
      name,
    }));
    vi.mocked(deleteTask).mockResolvedValue();
  });

  it("does not load Task history on authentication pages", () => {
    navigation.pathname = "/setup";
    render(<AppShell><div>Setup</div></AppShell>);
    expect(screen.getByText("Setup")).toBeInTheDocument();
    expect(listTasks).not.toHaveBeenCalled();
    expect(getCurrentUser).not.toHaveBeenCalled();
  });

  it("lists backend Task hrefs and marks PENDING/RUNNING status", async () => {
    render(<AppShell><div>Chat</div></AppShell>);
    expect(await screen.findByRole("link", { name: "Python Task" })).toHaveAttribute("href", "/chat?task=7");
    expect(screen.getByRole("link", { name: "Running Task" })).toHaveAttribute("href", "/chat?task=8");
    expect(screen.getByRole("status", { name: "Task is running" })).toHaveAttribute("data-status", "RUNNING");
  });

  it("refreshes history for task:created, task:status, and local Task events", async () => {
    vi.mocked(listTasks)
      .mockResolvedValueOnce({ tasks: [firstTask] })
      .mockResolvedValueOnce({ tasks: [runningTask] })
      .mockResolvedValueOnce({ tasks: [firstTask] })
      .mockResolvedValueOnce({ tasks: [runningTask] });
    render(<AppShell><div>Chat</div></AppShell>);
    await screen.findByRole("link", { name: "Python Task" });

    socket.handlers["task:created"]?.({ task: { ...runningTask } });
    expect(await screen.findByRole("link", { name: "Running Task" })).toBeInTheDocument();
    socket.handlers["task:status"]?.({ task: { ...firstTask } });
    expect(await screen.findByRole("link", { name: "Python Task" })).toBeInTheDocument();
    window.dispatchEvent(new Event("auto-reign:tasks-changed"));
    expect(await screen.findByRole("link", { name: "Running Task" })).toBeInTheDocument();
    expect(listTasks).toHaveBeenCalledTimes(4);
  });

  it("renames a Task by numeric id", async () => {
    render(<AppShell><div>Chat</div></AppShell>);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Python Task" }));
    clickMenuItem(/rename/i);
    fireEvent.change(screen.getByLabelText("Task name"), { target: { value: "Renamed Task" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(renameTask).toHaveBeenCalledWith(7, "Renamed Task"));
    expect(await screen.findByRole("link", { name: "Renamed Task" })).toHaveAttribute("href", "/chat?task=7");
  });

  it("deletes the current Task selected by the task query and routes to new chat", async () => {
    window.history.replaceState(null, "", "/chat?task=7");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(listTasks)
      .mockResolvedValueOnce({ tasks: [firstTask] })
      .mockResolvedValueOnce({ tasks: [] });
    render(<AppShell><div>Chat</div></AppShell>);
    fireEvent.click(await screen.findByRole("button", { name: "Actions for Python Task" }));
    clickMenuItem(/delete/i);
    await waitFor(() => expect(deleteTask).toHaveBeenCalledWith(7));
    expect(navigation.replace).toHaveBeenCalledWith("/chat");
    confirm.mockRestore();
  });

  it("preserves account controls and logout", async () => {
    render(<AppShell><div>Chat</div></AppShell>);
    fireEvent.click(await screen.findByRole("button", { name: "alice" }));
    fireEvent.click(screen.getByRole("button", { name: "Log out" }));
    expect(clearAuthToken).toHaveBeenCalledTimes(1);
    expect(navigation.replace).toHaveBeenCalledWith("/login");
  });
});
