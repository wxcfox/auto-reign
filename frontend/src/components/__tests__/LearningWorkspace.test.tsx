import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LearningWorkspace } from "../LearningWorkspace";
import i18next from "@/i18n/setup";
import { getModels, recordLearningNoteStream } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getModels: vi.fn(),
  recordLearningNoteStream: vi.fn(),
}));

const learningResponse = {
  source: {
    artifact_id: "source-1",
    relative_path: "sources/learning-note.md",
    duplicate: false,
  },
  artifact: {
    id: "artifact-1",
    kind: "knowledge",
    relative_path: "knowledge/redis.md",
    display_name: "redis.md",
    revision: 1,
    processing_status: "completed",
    index_status: "completed",
    recovery_required: false,
    allowed_operations: ["replace_body"],
  },
  summary: {
    title: "Redis cache",
    summary: "Redis cache penetration and Bloom filters.",
    key_points: ["Bloom filters"],
    interview_takeaways: ["Explain cache protection clearly."],
    follow_up_questions: ["How do you avoid stale cache?"],
  },
};

describe("LearningWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    i18next.changeLanguage("en");
    vi.mocked(getModels).mockResolvedValue({
      providers: [{ provider: "qwen", models: ["qwen3.7-plus"] }],
    });
    vi.mocked(recordLearningNoteStream).mockImplementation(async (_payload, handlers) => {
      handlers.onDelta("# Redis cache\n\n## Summary\n\nRedis cache penetration and Bloom filters.");
      return learningResponse;
    });
  });

  it("renders an empty learning chat with composer and model selector", async () => {
    render(<LearningWorkspace />);

    expect(await screen.findByText(/What did you learn today/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Select model/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Message Auto Reign/i)).toBeInTheDocument();
  });

  it("records a learning note and keeps the streamed summary in the chat flow", async () => {
    render(<LearningWorkspace />);

    fireEvent.change(await screen.findByLabelText(/Message Auto Reign/i), {
      target: { value: "今天学习了 Redis 缓存穿透和布隆过滤器。" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Record learning/i }));

    expect(await screen.findByText(/今天学习了 Redis/)).toBeInTheDocument();
    expect(await screen.findByText(/Redis cache penetration/i)).toBeInTheDocument();
    expect(screen.getByText(/knowledge\/redis.md/)).toBeInTheDocument();
    expect(recordLearningNoteStream).toHaveBeenCalledWith(
      expect.objectContaining({
        text: "今天学习了 Redis 缓存穿透和布隆过滤器。",
        provider: "qwen",
        model: "qwen3.7-plus",
      }),
      expect.any(Object),
    );
  });

  it("renders the final Chinese learning summary with Chinese section headings", async () => {
    i18next.changeLanguage("zh-CN");
    vi.mocked(recordLearningNoteStream).mockImplementation(async (_payload, handlers) => {
      handlers.onDelta("# Spring Bean lifecycle\n\n## Summary\n\nMixed heading stream.");
      return {
        ...learningResponse,
        artifact: {
          ...learningResponse.artifact,
          relative_path: "knowledge/spring-bean-生命周期.md",
          display_name: "spring-bean-生命周期.md",
        },
        summary: {
          title: "Spring Bean 生命周期",
          summary: "总结了 Spring Bean 的完整生命周期流程。",
          key_points: ["核心流程：实例化 -> 注入 -> Aware -> 初始化 -> 销毁。"],
          interview_takeaways: ["面试中重点说明 BeanPostProcessor 的前后置处理。"],
          follow_up_questions: ["AOP 代理对象在哪个阶段生成？"],
        },
      };
    });

    render(<LearningWorkspace />);

    fireEvent.change(await screen.findByLabelText(/Message Auto Reign/i), {
      target: {
        value: "随手记：spring bean 生命周期: 实例化 -> 注入 -> Aware -> 初始化 -> 销毁",
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /记录学习/i }));

    expect(await screen.findByText("Spring Bean 生命周期")).toBeInTheDocument();
    expect(screen.getByText("摘要")).toBeInTheDocument();
    expect(screen.getByText("关键点")).toBeInTheDocument();
    expect(screen.getByText("面试表达")).toBeInTheDocument();
    expect(screen.getByText("可追问问题")).toBeInTheDocument();
    expect(screen.queryByText("Summary")).not.toBeInTheDocument();
    expect(recordLearningNoteStream).toHaveBeenCalledWith(
      expect.objectContaining({ language: "zh-CN" }),
      expect.any(Object),
    );
  });
});
