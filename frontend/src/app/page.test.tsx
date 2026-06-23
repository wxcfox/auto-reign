import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DashboardPage from "./page";
import { getHealth, getPreparationTasks, getWorkspaceArtifacts, getWorkspaceStatus } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getHealth: vi.fn(),
  getPreparationTasks: vi.fn(),
  getWorkspaceArtifact: vi.fn(),
  getWorkspaceArtifacts: vi.fn(),
  getWorkspaceStatus: vi.fn(),
}));

describe("DashboardPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getHealth).mockResolvedValue({
      status: "ok",
      storage: { mysql: "ok", qdrant: "ok" },
      providers: { openai: false, deepseek: false, qwen: true },
      workspace: { initialized: true },
    });
    vi.mocked(getWorkspaceStatus).mockResolvedValue({
      schema_version: 1,
      language: "zh-CN",
      artifact_count: 3,
      initialized: true,
    });
    vi.mocked(getPreparationTasks).mockResolvedValue({
      tasks: [
        {
          title: "MySQL：用 30 秒说清 redo/binlog 两阶段提交",
          reason: "来自当前学习计划",
          source_artifact_id: "plan-1",
          source_relative_path: "state/plan.md",
        },
        {
          title: "Spring：复述 Bean 生命周期",
          reason: "来自当前学习计划",
          source_artifact_id: "plan-1",
          source_relative_path: "state/plan.md",
        },
      ],
    });
    vi.mocked(getWorkspaceArtifacts).mockResolvedValue({
      artifacts: [
        {
          id: "source-1",
          kind: "source",
          relative_path: "sources/documents/resume.md",
          display_name: "resume.md",
          revision: 1,
          processing_status: "completed",
          index_status: "completed",
          recovery_required: false,
          allowed_operations: [],
        },
        {
          id: "knowledge-1",
          kind: "knowledge",
          relative_path: "knowledge/redis.md",
          display_name: "redis.md",
          revision: 1,
          processing_status: "completed",
          index_status: "completed",
          recovery_required: false,
          allowed_operations: ["replace_body"],
        },
        {
          id: "practice-1",
          kind: "practice",
          relative_path: "practice/2026/06/session.md",
          display_name: "session.md",
          revision: 1,
          processing_status: "completed",
          index_status: "completed",
          recovery_required: false,
          allowed_operations: [],
        },
      ],
    });
  });

  it("renders current preparation tasks on the workbench", async () => {
    render(<DashboardPage />);

    expect(await screen.findByText("面试学习工作台")).toBeInTheDocument();
    expect(await screen.findByText("MySQL：用 30 秒说清 redo/binlog 两阶段提交")).toBeInTheDocument();
    expect(screen.getByText("Spring：复述 Bean 生命周期")).toBeInTheDocument();
    expect(screen.getAllByText("Start drill")[0]).toHaveAttribute("href", "/interview");
    expect(screen.getAllByText("View plan")[0]).toHaveAttribute("href", "/library/plan-1");
    expect(screen.getByText("原始资料")).toBeInTheDocument();
    expect(screen.getByText("知识卡片")).toBeInTheDocument();
    expect(screen.getByText("练习记录")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("3")).toBeInTheDocument());
    expect(screen.queryByText(/上传 -> 面试 -> 复盘/)).not.toBeInTheDocument();
  });
});
