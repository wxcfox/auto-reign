import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LearningWorkspace } from "../LearningWorkspace";
import i18next from "@/i18n/setup";
import { getConversation, getModels, recordLearningNoteStream } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getConversation: vi.fn(),
  getModels: vi.fn(),
  recordLearningNoteStream: vi.fn(),
}));

const learningResponse = {
  conversation_id: "learning-session",
  source: {
    artifact_id: "source-1",
    relative_path: "inbox/learning-note.md",
    duplicate: false,
  },
  artifact: {
    id: "artifact-1",
    kind: "knowledge",
    owner: "knowledge",
    relative_path: "knowledge/redis.md",
    display_name: "redis.md",
    revision: 1,
    processing_status: "completed",
    index_status: "completed",
    recovery_required: false,
    allowed_operations: ["replace_body"],
    created_at: "2026-06-24T00:00:00Z",
    updated_at: "2026-06-24T00:00:00Z",
  },
  summary: {
    title: "Redis cache",
    summary: "Redis cache penetration and Bloom filters.",
    key_points: ["Bloom filters"],
    interview_takeaways: ["Explain cache protection clearly."],
    follow_up_questions: ["How do you avoid stale cache?"],
  },
  card_markdown: [
    "- 我的理解：",
    "  今天学习了 Redis 缓存穿透和布隆过滤器。",
    "- 修正/补充：",
    "  - Redis cache penetration and Bloom filters.",
    "- 30 秒面试说法：",
    "  - Explain cache protection clearly.",
    "- 易混点：",
    "  - 暂无明确易混点，后续练习中补充。",
    "- 追问：",
    "  - How do you avoid stale cache?",
  ].join("\n"),
};

describe("LearningWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    i18next.changeLanguage("en");
    vi.mocked(getModels).mockResolvedValue({
      providers: [{ provider: "qwen", models: ["qwen3.7-plus"] }],
    });
    vi.mocked(getConversation).mockRejectedValue(new Error("not used"));
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
        conversation_id: undefined,
        text: "今天学习了 Redis 缓存穿透和布隆过滤器。",
        provider: "qwen",
        model: "qwen3.7-plus",
      }),
      expect.any(Object),
    );
  });

  it("loads an existing learning conversation and appends to it", async () => {
    vi.mocked(getConversation).mockResolvedValue({
      id: "learning-session",
      kind: "learning",
      title: "Redis cache learning",
      href: "/learn?session=learning-session",
      started_at: "2026-06-27T00:00:00Z",
      updated_at: "2026-06-27T00:00:00Z",
      last_message: "Redis cache penetration",
      messages: [
        {
          id: "message-1",
          role: "user",
          message_type: "learning_input",
          content: "今天学习了 Redis 缓存穿透。",
          created_at: "2026-06-27T00:00:00Z",
          metadata: {},
        },
        {
          id: "message-2",
          role: "assistant",
          message_type: "learning_summary",
          content: "# Redis cache\n\n- 30 秒面试说法：\n  - Explain cache clearly.",
          created_at: "2026-06-27T00:00:01Z",
          metadata: { artifact_path: "knowledge/redis.md" },
        },
      ],
    });

    render(<LearningWorkspace sessionId="learning-session" />);

    expect(await screen.findByText(/今天学习了 Redis/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Message Auto Reign/i), {
      target: { value: "继续学习布隆过滤器。" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Record learning/i }));

    expect(recordLearningNoteStream).toHaveBeenCalledWith(
      expect.objectContaining({ conversation_id: "learning-session" }),
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

    expect((await screen.findAllByText("Spring Bean 生命周期")).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/我的理解/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/修正\/补充/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/30 秒面试说法/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/易混点/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/追问/).length).toBeGreaterThan(0);
    expect(screen.queryByText("摘要")).not.toBeInTheDocument();
    expect(screen.queryByText("Summary")).not.toBeInTheDocument();
    expect(recordLearningNoteStream).toHaveBeenCalledWith(
      expect.objectContaining({ language: "zh-CN" }),
      expect.any(Object),
    );
  });
});
