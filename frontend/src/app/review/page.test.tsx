import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ReviewPage from "./page";
import i18next from "@/i18n/setup";
import { getReport, getReports, recordRealInterview } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getReport: vi.fn(),
  getReports: vi.fn(),
  recordRealInterview: vi.fn(),
}));

const artifact = {
  id: "artifact-1",
  kind: "interview_record",
  owner: "sources",
  relative_path: "raw/20260624-120000-real-interview.md",
  display_name: "20260624-120000.md",
  revision: 1,
  processing_status: "completed",
  index_status: "pending",
  recovery_required: false,
  allowed_operations: [],
  created_at: "2026-06-24T12:00:00Z",
  updated_at: "2026-06-24T12:00:00Z",
};

describe("ReviewPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    i18next.changeLanguage("zh-CN");
    vi.mocked(getReports).mockResolvedValue({ reports: [] });
    vi.mocked(getReport).mockResolvedValue({
      report: {
        id: "report-1",
        session_id: "session-1",
        report_path: "reports/report.md",
        summary: "summary",
        weaknesses: [],
        created_at: "2026-06-24T12:00:00Z",
      },
      content: "# Report",
    });
    vi.mocked(recordRealInterview).mockResolvedValue({
      raw_artifact: artifact,
      high_frequency_artifact: {
        ...artifact,
        id: "high-frequency-1",
        kind: "high_frequency",
        owner: "review",
        relative_path: "review/high-frequency.md",
      },
      status_artifact: {
        ...artifact,
        id: "status-1",
        kind: "review_status",
        owner: "review",
        relative_path: "review/status.md",
      },
      questions: ["Redis 缓存击穿怎么处理？"],
      weak_points: ["我：只说了加锁，没答好降级预案。"],
    });
  });

  it("records a pasted real interview and shows extracted review actions", async () => {
    render(<ReviewPage />);

    fireEvent.change(await screen.findByLabelText("粘贴真实面试原始记录"), {
      target: {
        value: "面试官：Redis 缓存击穿怎么处理？\n我：只说了加锁，没答好降级预案。",
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存真实面试复盘" }));

    await waitFor(() =>
      expect(recordRealInterview).toHaveBeenCalledWith({
        text: "面试官：Redis 缓存击穿怎么处理？\n我：只说了加锁，没答好降级预案。",
        language: "zh-CN",
      }),
    );
    expect(await screen.findByText("Redis 缓存击穿怎么处理？")).toBeInTheDocument();
    expect(screen.getByText(/没答好降级预案/)).toBeInTheDocument();
    expect(screen.getByText("raw/20260624-120000-real-interview.md")).toBeInTheDocument();
    expect(screen.getByText("review/high-frequency.md")).toBeInTheDocument();
    expect(screen.getByText("review/status.md")).toBeInTheDocument();
  });
});
