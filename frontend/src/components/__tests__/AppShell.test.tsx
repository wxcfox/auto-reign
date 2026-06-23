import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "../AppShell";
import i18next from "@/i18n/setup";
import { listInterviewSessions } from "@/lib/api";

vi.mock("next/navigation", () => ({
  usePathname: () => "/interview",
}));

vi.mock("@/lib/api", () => ({
  listInterviewSessions: vi.fn(),
}));

describe("AppShell", () => {
  function sessionResponse(label: string, status: "active" | "completed", id = `${status}-session`) {
    return {
      sessions: [
        {
          resumable: status === "active",
          session: {
            id,
            config_id: "config-1",
            status,
            current_round: 1,
            started_at: "2026-06-23T00:00:00Z",
            ended_at: status === "completed" ? "2026-06-23T00:10:00Z" : null,
            report_path: status === "completed" ? "reports/completed.md" : null,
          },
          config: {
            id: "config-1",
            target_company: "",
            target_role: "",
            job_description: "",
            extra_prompt: label,
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
      ],
    };
  }

  beforeEach(() => {
    vi.clearAllMocks();
    i18next.changeLanguage("en");
    try {
      window.localStorage?.clear();
    } catch {
      // Tests run without persistent localStorage.
    }
    document.documentElement.dataset.theme = "light";
    vi.mocked(listInterviewSessions).mockResolvedValue({
      sessions: [
        ...sessionResponse("Active backend interview", "active", "active-session").sessions,
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
    await waitFor(() => expect(screen.getByRole("button", { name: /简体中文/i })).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /Dark mode/i })).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("uses target-state buttons for language and theme settings", async () => {
    render(
      <AppShell>
        <div>Current page</div>
      </AppShell>,
    );

    fireEvent.click(screen.getByRole("button", { name: /Local user/i }));
    const languageButton = await screen.findByRole("button", { name: /简体中文/i });
    fireEvent.click(languageButton);

    expect(await screen.findByRole("button", { name: /English/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /本地用户/i })).toBeInTheDocument();

    const themeButton = screen.getByRole("button", { name: /深色模式/i });
    fireEvent.click(themeButton);

    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(screen.getByRole("button", { name: /浅色模式/i })).toBeInTheDocument();
  });

  it("refreshes sidebar history when interview sessions change", async () => {
    vi.mocked(listInterviewSessions)
      .mockResolvedValueOnce(sessionResponse("Initial backend interview", "active", "initial-session"))
      .mockResolvedValueOnce(sessionResponse("Completed refreshed interview", "completed", "done-session"));

    render(
      <AppShell>
        <div>Current page</div>
      </AppShell>,
    );

    expect(await screen.findByRole("link", { name: /Initial backend interview/i }))
      .toHaveAttribute("href", "/interview?session=initial-session");

    window.dispatchEvent(new Event("auto-reign:interview-sessions-changed"));

    expect(await screen.findByRole("button", { name: /Completed refreshed interview/i }))
      .toBeDisabled();
    expect(screen.queryByRole("link", { name: /Initial backend interview/i })).not.toBeInTheDocument();
    expect(listInterviewSessions).toHaveBeenCalledTimes(2);
  });
});
