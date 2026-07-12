import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkspaceList } from "../WorkspaceList";
import i18next from "@/i18n/setup";
import {
  createWorkspace,
  deleteWorkspace,
  listWorkspaces,
  updateWorkspace,
} from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { Workspace } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  createWorkspace: vi.fn(),
  deleteWorkspace: vi.fn(),
  listWorkspaces: vi.fn(),
  updateWorkspace: vi.fn(),
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
  can_manage: true,
  is_active: true,
  config: { workspace_type: "agent_home", initial_agents_md: "# Shared" },
};

function mockPrivateLists(
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
    await i18next.changeLanguage("en");
  });

  it("loads owned inactive and active global definitions in parallel, dedupes, and keeps shared rows read-only", async () => {
    mockPrivateLists([privateWorkspace, globalWorkspace], [globalWorkspace]);

    render(<WorkspaceList scope="private" />);

    await waitFor(() => {
      expect(listWorkspaces).toHaveBeenCalledWith("owned", { includeInactive: true });
      expect(listWorkspaces).toHaveBeenCalledWith("global");
    });
    expect(screen.getAllByText(globalWorkspace.name)).toHaveLength(1);
    expect(screen.queryByRole("link", { name: /open files for my memory/i }))
      .not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /open files for shared growth/i }))
      .toHaveAttribute("href", "/workspaces/global-ws");
    expect(screen.queryByRole("link", { name: /admin\/workspaces/i }))
      .not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /edit my memory/i })).toBeEnabled();
    expect(screen.queryByRole("button", { name: /edit shared growth/i }))
      .not.toBeInTheDocument();
  });

  it("creates only a private definition and disables every competing row action while the editor is open", async () => {
    mockPrivateLists();
    vi.mocked(createWorkspace).mockResolvedValue({
      ...privateWorkspace,
      id: "new-private",
      name: "Private home",
      is_active: true,
    });
    render(<WorkspaceList scope="private" />);
    await screen.findByText(privateWorkspace.name);

    fireEvent.click(screen.getByRole("button", { name: /^create workspace$/i }));
    const editor = screen.getByRole("region", { name: /^create workspace$/i });
    expect(
      screen
        .getAllByRole("button", { name: /^create workspace$/i })
        .some((button) => button.hasAttribute("disabled")),
    ).toBe(true);
    expect(screen.getByRole("button", { name: /edit my memory/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /enable my memory/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /delete my memory/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /open files for shared growth/i }))
      .toBeDisabled();
    expect(screen.queryByRole("link", { name: /open files for shared growth/i }))
      .not.toBeInTheDocument();

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
    await waitFor(() =>
      expect(
        screen.queryByRole("region", { name: /^create workspace$/i }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: /^create workspace$/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /edit my memory/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /enable my memory/i })).toBeEnabled();
    expect(screen.getByRole("button", { name: /delete my memory/i })).toBeEnabled();
    expect(screen.queryByRole("combobox", { name: /visibility|owner|scope/i }))
      .not.toBeInTheDocument();
  });

  it("uses global list and mutation authority and never emits an admin file route", async () => {
    vi.mocked(listWorkspaces).mockResolvedValue({ workspaces: [globalWorkspace] });
    vi.mocked(updateWorkspace).mockResolvedValue({
      ...globalWorkspace,
      config: { ...globalWorkspace.config, initial_agents_md: "# Future users" },
    });
    render(<WorkspaceList scope="global" />);

    await waitFor(() =>
      expect(listWorkspaces).toHaveBeenCalledWith("global", { includeInactive: true }),
    );
    expect(screen.getByRole("link", { name: /open files for shared growth/i }))
      .toHaveAttribute("href", "/workspaces/global-ws");
    fireEvent.click(screen.getByRole("button", { name: /edit shared growth/i }));
    const editor = screen.getByRole("region", { name: /edit shared growth/i });
    expect(within(editor).getByText(/initialize this workspace.*future/i))
      .toBeInTheDocument();
    fireEvent.change(within(editor).getByLabelText(/initial AGENTS\.md/i), {
      target: { value: "# Future users" },
    });
    fireEvent.click(within(editor).getByRole("button", { name: /^save$/i }));

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
    expect(screen.queryByRole("link", { name: /admin\/workspaces/i }))
      .not.toBeInTheDocument();
  });

  it("reactivates an inactive definition once and keeps its full config", async () => {
    let resolveUpdate!: (value: Workspace) => void;
    vi.mocked(listWorkspaces).mockResolvedValue({ workspaces: [privateWorkspace] });
    vi.mocked(updateWorkspace).mockReturnValue(
      new Promise<Workspace>((resolve) => {
        resolveUpdate = resolve;
      }),
    );
    render(<WorkspaceList scope="private" />);
    await screen.findByText(privateWorkspace.name);

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
    mockPrivateLists([{ ...privateWorkspace, can_manage: false }], []);
    render(<WorkspaceList scope="private" />);
    await screen.findByText(privateWorkspace.name);

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
      workspaces:
        listScope === "owned" && !deleted ? [privateWorkspace] : [],
    }));
    vi.mocked(deleteWorkspace).mockImplementation(async () => {
      deleted = true;
      return { id: privateWorkspace.id, status: "deleted" as const };
    });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<WorkspaceList scope="private" />);
    await screen.findByText(privateWorkspace.name);

    fireEvent.click(screen.getByRole("button", { name: /delete my memory/i }));

    await waitFor(() =>
      expect(screen.queryByText(privateWorkspace.name)).not.toBeInTheDocument(),
    );
    expect(deleteWorkspace).toHaveBeenCalledWith("private", privateWorkspace.id);
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    confirmSpy.mockRestore();
  });

  it("does not delete when confirmation is cancelled", async () => {
    mockPrivateLists();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<WorkspaceList scope="private" />);
    await screen.findByText(privateWorkspace.name);

    fireEvent.click(screen.getByRole("button", { name: /delete my memory/i }));

    expect(deleteWorkspace).not.toHaveBeenCalled();
    expect(screen.getByText(privateWorkspace.name)).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("retains the row and reports a stable resource_in_use delete error", async () => {
    mockPrivateLists();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(deleteWorkspace).mockRejectedValue(
      new ApiError("internal dependency detail", {
        code: "resource_in_use",
        status: 409,
      }),
    );
    render(<WorkspaceList scope="private" />);
    await screen.findByText(privateWorkspace.name);

    fireEvent.click(screen.getByRole("button", { name: /delete my memory/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/active Agent/i);
    expect(screen.getByText(privateWorkspace.name)).toBeInTheDocument();
    expect(screen.queryByText(/internal dependency detail/i)).not.toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it("ignores a stale private load after switching to global scope", async () => {
    let resolveOwned!: (value: { workspaces: Workspace[] }) => void;
    let resolveShared!: (value: { workspaces: Workspace[] }) => void;
    vi.mocked(listWorkspaces).mockImplementation((listScope, options) => {
      if (listScope === "owned") {
        return new Promise((resolve) => {
          resolveOwned = resolve;
        });
      }
      if (!options?.includeInactive) {
        return new Promise((resolve) => {
          resolveShared = resolve;
        });
      }
      return Promise.resolve({ workspaces: [globalWorkspace] });
    });
    const view = render(<WorkspaceList scope="private" />);

    view.rerender(<WorkspaceList scope="global" />);
    expect(await screen.findByText(globalWorkspace.name)).toBeInTheDocument();
    await act(async () => {
      resolveOwned({ workspaces: [privateWorkspace] });
      resolveShared({ workspaces: [] });
      await Promise.resolve();
    });
    expect(screen.queryByText(privateWorkspace.name)).not.toBeInTheDocument();
  });

  it("hides a ready private scope immediately while the next global scope is pending", async () => {
    let resolveGlobal!: (value: { workspaces: Workspace[] }) => void;
    vi.mocked(listWorkspaces).mockImplementation((listScope, options) => {
      if (listScope === "owned") {
        return Promise.resolve({ workspaces: [privateWorkspace] });
      }
      if (!options?.includeInactive) {
        return Promise.resolve({ workspaces: [globalWorkspace] });
      }
      return new Promise((resolve) => {
        resolveGlobal = resolve;
      });
    });
    const view = render(<WorkspaceList scope="private" />);
    await screen.findByText(privateWorkspace.name);
    expect(screen.getByText(globalWorkspace.name)).toBeInTheDocument();

    view.rerender(<WorkspaceList scope="global" />);

    expect(screen.getByRole("status")).toHaveTextContent(/loading/i);
    expect(screen.queryByText(privateWorkspace.name)).not.toBeInTheDocument();
    expect(screen.queryByText(globalWorkspace.name)).not.toBeInTheDocument();

    await act(async () => {
      resolveGlobal({ workspaces: [globalWorkspace] });
      await Promise.resolve();
    });
    expect(await screen.findByText(globalWorkspace.name)).toBeInTheDocument();
  });

  it("renders a recoverable stable load error", async () => {
    vi.mocked(listWorkspaces)
      .mockRejectedValueOnce(new Error("network secret"))
      .mockResolvedValue({ workspaces: [] });
    render(<WorkspaceList scope="global" />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not load/i);
    expect(screen.queryByText(/network secret/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /retry/i }));
    expect(await screen.findByText(/no workspaces yet/i)).toBeInTheDocument();
  });

  it("renders the global first-initialization boundary in Chinese", async () => {
    await i18next.changeLanguage("zh-CN");
    vi.mocked(listWorkspaces).mockResolvedValue({ workspaces: [globalWorkspace] });
    render(<WorkspaceList scope="global" />);

    expect(await screen.findByRole("heading", { name: "全局工作区管理" }))
      .toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /编辑 Shared growth/i }));
    expect(screen.getByText(/之后首次初始化.*不会覆盖.*AGENTS\.md/i))
      .toBeInTheDocument();
  });
});
