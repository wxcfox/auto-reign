import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "../AppShell";
import { listInterviewSessions } from "@/lib/api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/interview",
}));

vi.mock("@/lib/api", () => ({
  listInterviewSessions: vi.fn(),
}));

describe("AppShell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listInterviewSessions).mockResolvedValue({
      sessions: [
        {
          resumable: true,
          session: {
            id: "active-session",
            config_id: "config-1",
            status: "active",
            current_round: 1,
            started_at: "2026-06-23T00:00:00Z",
            ended_at: null,
            report_path: null,
          },
          config: {
            id: "config-1",
            target_company: "",
            target_role: "",
            job_description: "",
            extra_prompt: "Active backend interview",
            language: "en",
            mode: "comprehensive",
            chat_model_provider: "qwen",
            chat_model: "qwen3.7-plus",
            target_rounds: 2,
            is_last_used: false,
            updated_at: "2026-06-23T00:00:00Z",
          },
          turns: [],
        },
        {
          resumable: false,
          session: {
            id: "completed-session",
            config_id: "config-2",
            status: "completed",
            current_round: 1,
            started_at: "2026-06-23T00:01:00Z",
            ended_at: "2026-06-23T00:10:00Z",
            report_path: "reports/completed.md",
          },
          config: {
            id: "config-2",
            target_company: "",
            target_role: "",
            job_description: "",
            extra_prompt: "Completed backend interview",
            language: "en",
            mode: "comprehensive",
            chat_model_provider: "qwen",
            chat_model: "qwen3.7-plus",
            target_rounds: 1,
            is_last_used: false,
            updated_at: "2026-06-23T00:00:00Z",
          },
          turns: [],
        },
      ],
    });
  });

  it("renders one new interview entry, history, workbench, and merged user settings", async () => {
    render(
      <AppShell>
        <div>Current page</div>
      </AppShell>,
    );

    expect(screen.getAllByRole("link", { name: /New interview/i })).toHaveLength(1);
    expect(screen.getByRole("link", { name: /New learning/i })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: /Primary/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /^Interview$/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/Recent/i)).not.toBeInTheDocument();

    expect(await screen.findByRole("link", { name: /Active backend interview/i }))
      .toHaveAttribute("href", "/interview?session=active-session");
    expect(screen.getByRole("button", { name: /Completed backend interview/i }))
      .toBeDisabled();

    const moreButton = screen.getByRole("button", { name: /More/i });
    expect(moreButton).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(moreButton);
    expect(moreButton).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: /Workbench/i })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Review/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Local user/i }));
    await waitFor(() => expect(screen.getByText(/Language/i)).toBeInTheDocument());
    expect(screen.getByText(/Dark mode/i)).toBeInTheDocument();
  });
});
