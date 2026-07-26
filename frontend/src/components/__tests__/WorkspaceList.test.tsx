import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkspaceList } from "../WorkspaceList";
import i18next from "@/i18n/setup";
import {
  createWorkspace,
  deleteWorkspace,
  getCurrentUser,
  listWorkspaces,
  updateWorkspace,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { User, Workspace } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  createWorkspace: vi.fn(),
  deleteWorkspace: vi.fn(),
  getCurrentUser: vi.fn(),
  listWorkspaces: vi.fn(),
  updateWorkspace: vi.fn(),
}));

vi.mock("@/components/WorkspaceBrowser", () => ({
  WorkspaceBrowser: (props: { workspaceId: string }) => (
    <div data-testid="workspace-browser-stub">browser:{props.workspaceId}</div>
  ),
}));

const privateWorkspace: Workspace = {
  id: "private-ws",
  name: "My memory",
  scope: "private",
  can_manage: true,
  is_active: false,
  config: { workspace_type: "agent_home", initial_agents_md: "# Mine" },
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const globalWorkspace: Workspace = {
  ...privateWorkspace,
  id: "global-ws",
  name: "Shared growth",
  scope: "global",
  can_manage: false,
  is_active: true,
  config: { workspace_type: "agent_home", initial_agents_md: "# Shared" },
};

function mockUser(role: User["role"] = "user") {
  vi.mocked(getCurrentUser).mockResolvedValue({
    id: 7,
    username: "alice",
    role,
  } as User);
}

function mockLists(
  owned: Workspace[] = [privateWorkspace],
  shared: Workspace[] = [globalWorkspace],
) {
  vi.mocked(listWorkspaces).mockImplementation(async (scope) => ({
    workspaces: scope === "owned" ? owned : shared,
  }));
}

describe("WorkspaceList management page", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    mockUser();
    await i18next.changeLanguage("en");
  });

  it("loads owned and public definitions into one flat list with no tabs", async () => {
    mockLists([privateWorkspace, globalWorkspace], [globalWorkspace]);

    render(<WorkspaceList />);

    await waitFor(() => {
      expect(listWorkspaces).toHaveBeenCalledWith("owned", { includeInactive: true });
      expect(listWorkspaces).toHaveBeenCalledWith("global");
    });
    await screen.findByRole("heading", { name: privateWorkspace.name });
    expect(
      screen.getByRole("button", { name: /open shared growth/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  });

  it("browses a public workspace by id alone and explains that its files are personal", async () => {
    mockLists([], [globalWorkspace]);

    render(<WorkspaceList />);
    await screen.findByRole("heading", { name: globalWorkspace.name });

    expect(screen.getByTestId("workspace-browser-stub")).toHaveTextContent(
      `browser:${globalWorkspace.id}`,
    );
    expect(screen.getByText(/files are yours alone/i)).toBeInTheDocument();
  });

  it("keeps a public definition read-only for a user who cannot manage it", async () => {
    mockLists([], [globalWorkspace]);

    render(<WorkspaceList />);
    await screen.findByRole("heading", { name: globalWorkspace.name });

    expect(screen.queryByRole("button", { name: /edit shared growth/i }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete shared growth/i }))
      .not.toBeInTheDocument();
    // Read-only definition, but the files still belong to the caller.
    expect(screen.getByTestId("workspace-browser-stub")).toBeInTheDocument();
  });

  it("lets an administrator edit a public definition from the same flat list", async () => {
    mockUser("admin");
    mockLists([], [{ ...globalWorkspace, can_manage: true }]);
    vi.mocked(updateWorkspace).mockResolvedValue({
      ...globalWorkspace,
      config: { ...globalWorkspace.config, initial_agents_md: "# Future users" },
    });

    render(<WorkspaceList />);
    await screen.findByRole("heading", { name: globalWorkspace.name });

    fireEvent.click(screen.getByRole("button", { name: /edit shared growth/i }));
    const editor = screen.getByRole("region", { name: /edit shared growth/i });
    fireEvent.change(within(editor).getByLabelText(/initial AGENTS\.md/i), {
      target: { value: "# Future users" },
    });
    fireEvent.click(within(editor).getByRole("button", { name: /^save$/i }));

    // The definition's own scope picks the admin mutation base.
    await waitFor(() =>
      expect(updateWorkspace).toHaveBeenCalledWith("global", globalWorkspace.id, {
        name: globalWorkspace.name,
        config: {
          workspace_type: "agent_home",
          initial_agents_md: "# Future users",
        },
        is_active: true,
      }),
    );
  });

  it("offers no publish choice to a non-admin and creates a private definition", async () => {
    mockLists();
    vi.mocked(createWorkspace).mockResolvedValue({
      ...privateWorkspace,
      id: "new-private",
      name: "Private home",
      is_active: true,
    });
    render(<WorkspaceList />);
    await screen.findByRole("heading", { name: privateWorkspace.name });

    fireEvent.click(screen.getByRole("button", { name: /^create workspace$/i }));
    const editor = screen.getByRole("region", { name: /^create workspace$/i });
    expect(within(editor).queryByLabelText(/availability/i)).not.toBeInTheDocument();

    fireEvent.change(within(editor).getByLabelText(/^name$/i), {
      target: { value: "Private home" },
    });
    fireEvent.change(within(editor).getByLabelText(/initial AGENTS\.md/i), {
      target: { value: "# Private rules" },
    });
    fireEvent.click(within(editor).getByRole("button", { name: /create workspace/i }));

    await waitFor(() =>
      expect(createWorkspace).toHaveBeenCalledWith("private", {
        name: "Private home",
        config: {
          workspace_type: "agent_home",
          initial_agents_md: "# Private rules",
        },
      }),
    );
  });

  it("lets an administrator publish a new definition through the scope selector", async () => {
    mockUser("admin");
    mockLists();
    vi.mocked(createWorkspace).mockResolvedValue({
      ...globalWorkspace,
      id: "new-global",
      name: "Team home",
    });
    render(<WorkspaceList />);
    await screen.findByRole("heading", { name: privateWorkspace.name });

    fireEvent.click(screen.getByRole("button", { name: /^create workspace$/i }));
    const editor = screen.getByRole("region", { name: /^create workspace$/i });
    fireEvent.change(within(editor).getByLabelText(/availability/i), {
      target: { value: "global" },
    });
    fireEvent.change(within(editor).getByLabelText(/^name$/i), {
      target: { value: "Team home" },
    });
    fireEvent.change(within(editor).getByLabelText(/initial AGENTS\.md/i), {
      target: { value: "# Team rules" },
    });
    fireEvent.click(within(editor).getByRole("button", { name: /create workspace/i }));

    await waitFor(() =>
      expect(createWorkspace).toHaveBeenCalledWith("global", {
        name: "Team home",
        config: {
          workspace_type: "agent_home",
          initial_agents_md: "# Team rules",
        },
      }),
    );
  });

  it("reactivates an inactive definition once and keeps its full config", async () => {
    let resolveUpdate!: (value: Workspace) => void;
    mockLists([privateWorkspace], []);
    vi.mocked(updateWorkspace).mockReturnValue(
      new Promise<Workspace>((resolve) => {
        resolveUpdate = resolve;
      }),
    );
    render(<WorkspaceList />);
    await screen.findByRole("heading", { name: privateWorkspace.name });

    const enable = screen.getByRole("button", { name: /enable my memory/i });
    fireEvent.click(enable);
    fireEvent.click(enable);
    expect(updateWorkspace).toHaveBeenCalledTimes(1);
    expect(updateWorkspace).toHaveBeenCalledWith("private", privateWorkspace.id, {
      name: privateWorkspace.name,
      config: privateWorkspace.config,
      is_active: true,
    });

    await act(async () => {
      resolveUpdate({ ...privateWorkspace, is_active: true });
      await Promise.resolve();
    });
  });

  it("never renders definition mutations when can_manage is false", async () => {
    mockLists([{ ...privateWorkspace, can_manage: false }], []);
    render(<WorkspaceList />);
    await screen.findByRole("heading", { name: privateWorkspace.name });

    expect(screen.queryByRole("button", { name: /edit my memory/i }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /enable my memory/i }))
      .not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete my memory/i }))
      .not.toBeInTheDocument();
  });

  it("confirms a successful private delete, then removes the row only after reloading", async () => {
    let deleted = false;
    vi.mocked(listWorkspaces).mockImplementation(async (listScope) => ({
      workspaces: listScope === "owned" && !deleted ? [privateWorkspace] : [],
    }));
    vi.mocked(deleteWorkspace).mockImplementation(async () => {
      deleted = true;
      return { id: privateWorkspace.id, status: "deleted" as const };
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<WorkspaceList />);
    await screen.findByRole("heading", { name: privateWorkspace.name });

    fireEvent.click(screen.getByRole("button", { name: /delete my memory/i }));

    await waitFor(() =>
      expect(screen.queryByText(privateWorkspace.name)).not.toBeInTheDocument(),
    );
    expect(deleteWorkspace).toHaveBeenCalledWith("private", privateWorkspace.id);
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    confirmSpy.mockRestore();
  });

  it("does not delete when confirmation is cancelled", async () => {
    mockLists([privateWorkspace], []);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<WorkspaceList />);
    await screen.findByRole("heading", { name: privateWorkspace.name });

    fireEvent.click(screen.getByRole("button", { name: /delete my memory/i }));

    expect(deleteWorkspace).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: privateWorkspace.name })).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("retains the row and reports a stable resource_in_use delete error", async () => {
    mockLists([privateWorkspace], []);
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(deleteWorkspace).mockRejectedValue(
      new ApiError("internal dependency detail", {
        code: "resource_in_use",
        status: 409,
      }),
    );
    render(<WorkspaceList />);
    await screen.findByRole("heading", { name: privateWorkspace.name });

    fireEvent.click(screen.getByRole("button", { name: /delete my memory/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/active Agent/i);
    expect(screen.getByRole("heading", { name: privateWorkspace.name })).toBeInTheDocument();
    expect(screen.queryByText(/internal dependency detail/i)).not.toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("renders a recoverable stable load error", async () => {
    vi.mocked(listWorkspaces)
      .mockRejectedValueOnce(new Error("network secret"))
      .mockResolvedValue({ workspaces: [] });
    render(<WorkspaceList />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load/i);
    expect(screen.queryByText(/network secret/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText(/no workspaces yet/i)).toBeInTheDocument();
  });

  it("renders the public template boundary in Chinese", async () => {
    await i18next.changeLanguage("zh-CN");
    mockLists([], [globalWorkspace]);
    render(<WorkspaceList />);

    await screen.findByRole("heading", { name: globalWorkspace.name });
    expect(screen.getByText(/文件属于你个人.*首次使用时写入/)).toBeInTheDocument();
  });
});
