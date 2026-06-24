import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InterviewWorkspace } from "../InterviewWorkspace";
import {
  createInterviewSessionStream,
  getInterviewSession,
  getLastInterviewConfig,
  getModels,
  nextQuestionStream,
  saveLastInterviewConfig,
  submitAnswerStream,
  submitFollowUpAnswerStream,
} from "@/lib/api";

vi.mock("@/lib/api", () => ({
  createInterviewSessionStream: vi.fn(),
  getInterviewSession: vi.fn(),
  getLastInterviewConfig: vi.fn(),
  getModels: vi.fn(),
  nextQuestionStream: vi.fn(),
  saveLastInterviewConfig: vi.fn(),
  submitAnswerStream: vi.fn(),
  submitFollowUpAnswerStream: vi.fn(),
}));

const baseConfig = {
  id: "config-1",
  target_company: "",
  target_role: "",
  job_description: "",
  extra_prompt: "",
  language: "en" as const,
  mode: "comprehensive" as const,
  chat_model_provider: "qwen" as const,
  chat_model: "qwen3.7-plus",
  target_rounds: 3,
  is_last_used: true,
  updated_at: "2026-06-23T00:00:00Z",
};

const baseSession = {
  id: "session-1",
  config_id: "config-1",
  status: "active" as const,
  current_round: 1,
  started_at: "2026-06-23T00:00:00Z",
  ended_at: null,
  report_path: null,
};

const firstTurn = {
  id: "turn-1",
  session_id: "session-1",
  round_index: 1,
  question: "How would you explain your caching strategy?",
  answer: null,
  feedback: null,
  missing_points: [],
  follow_up_question: null,
  follow_up_answer: null,
  follow_up_feedback: null,
  follow_up_missing_points: [],
  follow_up_weaknesses: [],
  follow_up_review_suggestions: [],
  weaknesses: [],
  review_suggestions: [],
  retrieved_context_refs: [],
  created_at: "2026-06-23T00:00:00Z",
};

const secondTurn = {
  ...firstTurn,
  id: "turn-2",
  round_index: 2,
  question: "What tradeoffs did you make when traffic increased?",
  answer: null,
  feedback: null,
  missing_points: [],
  follow_up_question: null,
};

describe("InterviewWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(getModels).mockResolvedValue({
      providers: [{ provider: "qwen", models: ["qwen3.7-plus"] }],
    });
    vi.mocked(getLastInterviewConfig).mockResolvedValue(baseConfig);
    vi.mocked(saveLastInterviewConfig).mockResolvedValue(baseConfig);
    vi.mocked(getInterviewSession).mockRejectedValue(new Error("not used"));
    vi.mocked(submitFollowUpAnswerStream).mockResolvedValue({
      feedback: "Follow-up feedback",
      missing_points: [],
      weaknesses: [],
      review_suggestions: [],
      better_answer: "",
      mastery_change: "unchanged",
      should_write_weakness: false,
      should_write_high_frequency: false,
      tested_points: [],
    });
  });

  it("renders a centered empty chat state with model controls and composer", async () => {
    render(<InterviewWorkspace />);

    expect(await screen.findByText(/Ready when you are/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Select model/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Message Auto Reign/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Interview settings/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Target company/i)).not.toBeInTheDocument();
  });

  it("starts an interview from natural language context in the composer", async () => {
    vi.mocked(createInterviewSessionStream).mockImplementation(async (_config, handlers) => {
      handlers.onDelta(firstTurn.question);
      return {
        session: baseSession,
        turn: firstTurn,
      };
    });

    render(<InterviewWorkspace />);

    fireEvent.change(await screen.findByLabelText(/Message Auto Reign/i), {
      target: {
        value: "面试 Acme 后端工程师，JD 关注缓存、高并发和 FastAPI。",
      },
    });
    fireEvent.click(screen.getByRole("button", { name: /Start interview/i }));

    expect(await screen.findByText(firstTurn.question)).toBeInTheDocument();
    expect(screen.getByText(/面试 Acme 后端工程师/)).toBeInTheDocument();
    expect(saveLastInterviewConfig).toHaveBeenCalledWith(
      expect.objectContaining({
        target_company: "",
        target_role: "",
        job_description: "",
        extra_prompt: "面试 Acme 后端工程师，JD 关注缓存、高并发和 FastAPI。",
      }),
    );
  });

  it("can start a generic interview without any typed target context", async () => {
    vi.mocked(createInterviewSessionStream).mockResolvedValue({
      session: baseSession,
      turn: firstTurn,
    });

    render(<InterviewWorkspace />);

    fireEvent.click(await screen.findByRole("button", { name: /Start interview/i }));

    expect(await screen.findByText(firstTurn.question)).toBeInTheDocument();
    expect(saveLastInterviewConfig).toHaveBeenCalledWith(
      expect.objectContaining({
        target_company: "",
        target_role: "",
        job_description: "",
        extra_prompt: "",
      }),
    );
  });

  it("keeps earlier interview turns visible and automatically streams the next question after follow-up", async () => {
    vi.mocked(createInterviewSessionStream).mockImplementation(async (_config, handlers) => {
      handlers.onDelta(firstTurn.question);
      return {
        session: baseSession,
        turn: firstTurn,
      };
    });
    vi.mocked(submitAnswerStream).mockImplementation(async (_sessionId, _answer, handlers) => {
      handlers.onDelta("Use concrete cache invalidation examples.");
      return {
        feedback: "Use concrete cache invalidation examples.",
        missing_points: ["Eviction policy"],
        follow_up_question: "How would you handle stale data?",
        weaknesses: ["Needs operational detail"],
        review_suggestions: ["Prepare one production cache incident"],
        better_answer: "I would explain cache stampede with mutex locks and logical expiration.",
        mastery_change: "basic",
        should_write_weakness: true,
        should_write_high_frequency: true,
        tested_points: ["Cache stampede", "Operational tradeoffs"],
      };
    });
    vi.mocked(submitFollowUpAnswerStream).mockImplementation(async (_sessionId, _answer, handlers) => {
      handlers.onDelta("Follow-up feedback.");
      return {
        feedback: "Follow-up feedback.",
        missing_points: [],
        weaknesses: [],
        review_suggestions: [],
        better_answer: "",
        mastery_change: "unchanged",
        should_write_weakness: false,
        should_write_high_frequency: false,
        tested_points: [],
      };
    });
    vi.mocked(nextQuestionStream).mockImplementation(async (_sessionId, handlers) => {
      handlers.onDelta(secondTurn.question);
      return {
        session: { ...baseSession, current_round: 2 },
        turn: secondTurn,
      };
    });

    render(<InterviewWorkspace />);

    fireEvent.change(await screen.findByLabelText(/Message Auto Reign/i), {
      target: { value: "Acme backend interview focused on caching, 2 rounds." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Start interview/i }));

    expect(await screen.findByText(firstTurn.question)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Next question/i })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Message Auto Reign/i), {
      target: { value: "I use Redis with TTLs and metrics." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send answer/i }));

    await waitFor(() =>
      expect(screen.getByText(/Use concrete cache invalidation examples/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/I would explain cache stampede/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Cache stampede/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/basic/i)).toBeInTheDocument();
    expect(screen.getByText(/write weakness/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Message Auto Reign/i), {
      target: { value: "I would monitor stale reads and cache hit rate." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send follow-up/i }));

    await waitFor(() => expect(screen.getByText(secondTurn.question)).toBeInTheDocument());
    expect(screen.getByText(firstTurn.question)).toBeInTheDocument();
    expect(screen.getByText(/I use Redis with TTLs and metrics/i)).toBeInTheDocument();
    expect(screen.getByText(/Use concrete cache invalidation examples/i)).toBeInTheDocument();
    expect(screen.getByText(/I would monitor stale reads/i)).toBeInTheDocument();
    expect(screen.getByText("Follow-up feedback.")).toBeInTheDocument();
    expect(nextQuestionStream).toHaveBeenCalledWith("session-1", expect.any(Object), "");
  });

  it("automatically streams the next question when the model does not ask a follow-up", async () => {
    vi.mocked(getLastInterviewConfig).mockResolvedValue({ ...baseConfig, target_rounds: 2 });
    vi.mocked(createInterviewSessionStream).mockResolvedValue({
      session: { ...baseSession, current_round: 1 },
      turn: firstTurn,
    });
    vi.mocked(submitAnswerStream).mockResolvedValue({
      feedback: "Good answer; no follow-up needed.",
      missing_points: [],
      follow_up_question: "",
      weaknesses: [],
      review_suggestions: [],
      better_answer: "",
      mastery_change: "unchanged",
      should_write_weakness: false,
      should_write_high_frequency: false,
      tested_points: [],
    });
    vi.mocked(nextQuestionStream).mockImplementation(async (_sessionId, handlers) => {
      handlers.onDelta(secondTurn.question);
      return {
        session: { ...baseSession, current_round: 2 },
        turn: secondTurn,
      };
    });

    render(<InterviewWorkspace />);

    fireEvent.change(await screen.findByLabelText(/Message Auto Reign/i), {
      target: { value: "2 rounds" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Start interview/i }));
    expect(await screen.findByText(firstTurn.question)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Message Auto Reign/i), {
      target: { value: "I would use TTLs and cache metrics." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send answer/i }));

    expect(await screen.findByText(secondTurn.question)).toBeInTheDocument();
    expect(screen.getByText(firstTurn.question)).toBeInTheDocument();
    expect(nextQuestionStream).toHaveBeenCalledWith("session-1", expect.any(Object), "");
  });

  it("does not auto-finish after the requested round and lets the user continue naturally", async () => {
    vi.mocked(getLastInterviewConfig).mockResolvedValue({ ...baseConfig, target_rounds: 1 });
    vi.mocked(createInterviewSessionStream).mockResolvedValue({
      session: { ...baseSession, current_round: 1 },
      turn: firstTurn,
    });
    vi.mocked(submitAnswerStream).mockResolvedValue({
      feedback: "Final feedback.",
      missing_points: [],
      follow_up_question: "",
      weaknesses: [],
      review_suggestions: [],
      better_answer: "",
      mastery_change: "unchanged",
      should_write_weakness: false,
      should_write_high_frequency: false,
      tested_points: [],
    });
    vi.mocked(nextQuestionStream).mockImplementation(async (_sessionId, handlers) => {
      handlers.onDelta(secondTurn.question);
      return {
        session: { ...baseSession, current_round: 2 },
        turn: secondTurn,
      };
    });

    render(<InterviewWorkspace />);

    fireEvent.click(await screen.findByRole("button", { name: /Start interview/i }));
    expect(await screen.findByText(firstTurn.question)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Message Auto Reign/i), {
      target: { value: "I would give a concise final answer." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send answer/i }));

    expect(await screen.findByText(/Final feedback/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Retry report/i })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Message Auto Reign/i), {
      target: { value: "再来一题，问 MySQL" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Next question/i }));

    await waitFor(() =>
      expect(nextQuestionStream).toHaveBeenCalledWith(
        "session-1",
        expect.any(Object),
        "再来一题，问 MySQL",
      ),
    );
    expect(await screen.findByText(secondTurn.question)).toBeInTheDocument();
  });

  it("loads an existing active interview session from history", async () => {
    vi.mocked(getInterviewSession).mockResolvedValue({
      session: baseSession,
      config: baseConfig,
      turns: [
        {
          ...firstTurn,
          answer: "I would use Redis with careful invalidation.",
          feedback: "Good detail.",
        },
      ],
    });

    render(<InterviewWorkspace sessionId="session-1" />);

    expect(await screen.findByText(firstTurn.question)).toBeInTheDocument();
    expect(screen.getByText(/I would use Redis/i)).toBeInTheDocument();
    expect(screen.getByText(/Good detail/i)).toBeInTheDocument();
    expect(getInterviewSession).toHaveBeenCalledWith("session-1");
  });

  it("loads a completed interview from history without enabling new chat input", async () => {
    vi.mocked(getInterviewSession).mockResolvedValue({
      session: { ...baseSession, status: "completed", ended_at: "2026-06-23T00:10:00Z" },
      config: baseConfig,
      turns: [
        {
          ...firstTurn,
          answer: "I would use Redis with careful invalidation.",
          feedback: "Good detail.",
        },
      ],
    });

    render(<InterviewWorkspace sessionId="session-1" />);

    expect(await screen.findByText(firstTurn.question)).toBeInTheDocument();
    expect(screen.getByLabelText(/Message Auto Reign/i)).toBeDisabled();
  });

  it("shows an assistant loading state while feedback is being generated", async () => {
    let resolveFeedback: ((value: Awaited<ReturnType<typeof submitAnswerStream>>) => void) | undefined;
    vi.mocked(createInterviewSessionStream).mockResolvedValue({
      session: baseSession,
      turn: firstTurn,
    });
    vi.mocked(submitAnswerStream).mockReturnValue(
      new Promise((resolve) => {
        resolveFeedback = resolve;
      }),
    );

    render(<InterviewWorkspace />);

    fireEvent.click(await screen.findByRole("button", { name: /Start interview/i }));

    expect(await screen.findByText(firstTurn.question)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Message Auto Reign/i), {
      target: { value: "I would explain the tradeoff clearly." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send answer/i }));

    expect(await screen.findByText(/Generating feedback/i)).toBeInTheDocument();

    resolveFeedback?.({
      feedback: "Good structure; add a sharper example.",
      missing_points: [],
      follow_up_question: "What would you measure?",
      weaknesses: [],
      review_suggestions: [],
      better_answer: "",
      mastery_change: "unchanged",
      should_write_weakness: false,
      should_write_high_frequency: false,
      tested_points: [],
    });
    await waitFor(() =>
      expect(screen.getByText(/Good structure; add a sharper example/i)).toBeInTheDocument(),
    );
  });

  it("renders streamed answer deltas before replacing them with final feedback", async () => {
    let resolveStream: (() => void) | undefined;
    vi.mocked(createInterviewSessionStream).mockResolvedValue({
      session: baseSession,
      turn: firstTurn,
    });
    vi.mocked(submitAnswerStream).mockImplementation(async (_sessionId, _answer, handlers) => {
      handlers.onDelta("Streaming ");
      handlers.onDelta("feedback");
      await new Promise<void>((resolve) => {
        resolveStream = resolve;
      });
      return {
        feedback: "Final structured feedback.",
        missing_points: [],
        follow_up_question: "What would you measure?",
        weaknesses: [],
        review_suggestions: [],
        better_answer: "",
        mastery_change: "unchanged",
        should_write_weakness: false,
        should_write_high_frequency: false,
        tested_points: [],
      };
    });

    render(<InterviewWorkspace />);

    fireEvent.click(await screen.findByRole("button", { name: /Start interview/i }));

    expect(await screen.findByText(firstTurn.question)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Message Auto Reign/i), {
      target: { value: "I use Redis with TTLs and metrics." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send answer/i }));

    expect(await screen.findByText(/Streaming feedback/i)).toBeInTheDocument();
    resolveStream?.();
    await waitFor(() =>
      expect(screen.getByText(/Final structured feedback/i)).toBeInTheDocument(),
    );
  });
});
