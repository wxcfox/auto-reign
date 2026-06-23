import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LearningWorkspace } from "../LearningWorkspace";
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
});
