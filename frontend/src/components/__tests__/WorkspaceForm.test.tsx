import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkspaceForm } from "../WorkspaceForm";
import i18next from "@/i18n/setup";
import { createWorkspace, updateWorkspace } from "@/lib/api";
import { ApiError } from "@/lib/api-error";
import type { Workspace } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  createWorkspace: vi.fn(),
  updateWorkspace: vi.fn(),
}));

const workspace: Workspace = {
  id: "ws-1",
  name: "Growth memory",
  scope: "private",
  can_manage: true,
  is_active: false,
  config: { workspace_type: "agent_home", initial_agents_md: "# Rules" },
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

function fill(name: string, instructions: string) {
  fireEvent.change(screen.getByLabelText(/^name$/i), { target: { value: name } });
  fireEvent.change(screen.getByLabelText(/initial AGENTS\.md/i), {
    target: { value: instructions },
  });
}

describe("WorkspaceForm", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    await i18next.changeLanguage("en");
  });

  it("routes a global create through the global API and explains create-only initialization", async () => {
    const onSaved = vi.fn();
    vi.mocked(createWorkspace).mockResolvedValue({ ...workspace, scope: "global" });
    render(<WorkspaceForm scope="global" onSaved={onSaved} />);

    expect(screen.getByText(/initialize this workspace.*first time.*future/i))
      .toBeInTheDocument();
    fill("  Shared growth  ", "  # Shared rules  ");
    fireEvent.click(screen.getByRole("button", { name: /create workspace/i }));

    await waitFor(() =>
      expect(createWorkspace).toHaveBeenCalledWith("global", {
        name: "Shared growth",
        config: { workspace_type: "agent_home", initial_agents_md: "# Shared rules" },
      }),
    );
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ id: workspace.id }));
    expect(screen.queryByRole("combobox", { name: /visibility|owner|scope/i }))
      .not.toBeInTheDocument();
  });

  it("edits the definition while preserving its inactive lifecycle state", async () => {
    const onSaved = vi.fn();
    const updated = { ...workspace, name: "Evolved memory" };
    vi.mocked(updateWorkspace).mockResolvedValue(updated);
    render(
      <WorkspaceForm
        onSaved={onSaved}
        scope="private"
        workspace={workspace}
      />,
    );

    fill("Evolved memory", "# Future initialization only");
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(updateWorkspace).toHaveBeenCalledWith("private", workspace.id, {
        name: "Evolved memory",
        config: {
          workspace_type: "agent_home",
          initial_agents_md: "# Future initialization only",
        },
        is_active: false,
      }),
    );
    expect(screen.queryByRole("checkbox", { name: /active|启用/i })).not.toBeInTheDocument();
    expect(onSaved).toHaveBeenCalledWith(updated);
  });

  it("keeps the draft and localizes a resource name conflict", async () => {
    vi.mocked(createWorkspace).mockRejectedValue(
      new ApiError("internal detail", { code: "resource_name_taken", status: 409 }),
    );
    render(<WorkspaceForm scope="private" />);

    fill("My home", "# My rules");
    fireEvent.click(screen.getByRole("button", { name: /create workspace/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already exists/i);
    expect(screen.getByLabelText(/^name$/i)).toHaveValue("My home");
    expect(screen.queryByText(/internal detail/i)).not.toBeInTheDocument();
  });

  it("coalesces duplicate submits and ignores a completion after unmount", async () => {
    let resolveCreate!: (value: Workspace) => void;
    vi.mocked(createWorkspace).mockReturnValue(
      new Promise<Workspace>((resolve) => {
        resolveCreate = resolve;
      }),
    );
    const onSaved = vi.fn();
    const view = render(<WorkspaceForm scope="private" onSaved={onSaved} />);
    fill("My home", "# My rules");

    const form = screen.getByRole("button", { name: /create workspace/i }).closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);
    fireEvent.submit(form!);
    expect(createWorkspace).toHaveBeenCalledTimes(1);

    view.unmount();
    await act(async () => {
      resolveCreate(workspace);
      await Promise.resolve();
    });
    expect(onSaved).not.toHaveBeenCalled();
  });

  it.each(["create", "update"] as const)(
    "ignores an old %s completion after rerendering into a new scope",
    async (operation) => {
      let resolveSave!: (value: Workspace) => void;
      const pendingSave = new Promise<Workspace>((resolve) => {
        resolveSave = resolve;
      });
      if (operation === "create") {
        vi.mocked(createWorkspace).mockReturnValue(pendingSave);
      } else {
        vi.mocked(updateWorkspace).mockReturnValue(pendingSave);
      }
      const onSaved = vi.fn();
      const onSavingChange = vi.fn();
      const editedWorkspace = operation === "update" ? workspace : null;
      const view = render(
        <WorkspaceForm
          onSaved={onSaved}
          onSavingChange={onSavingChange}
          scope="private"
          workspace={editedWorkspace}
        />,
      );
      fill("Scope-bound memory", "# Scope-bound rules");

      fireEvent.click(
        screen.getByRole("button", {
          name: operation === "create" ? /create workspace/i : /^save$/i,
        }),
      );

      expect(
        operation === "create" ? createWorkspace : updateWorkspace,
      ).toHaveBeenCalledTimes(1);
      expect(onSavingChange).toHaveBeenLastCalledWith(true);

      view.rerender(
        <WorkspaceForm
          onSaved={onSaved}
          onSavingChange={onSavingChange}
          scope="global"
          workspace={editedWorkspace}
        />,
      );
      await waitFor(() => expect(onSavingChange).toHaveBeenLastCalledWith(false));
      onSavingChange.mockClear();

      await act(async () => {
        resolveSave(workspace);
        await Promise.resolve();
      });

      expect(onSaved).not.toHaveBeenCalled();
      expect(onSavingChange).not.toHaveBeenCalled();
    },
  );
});
