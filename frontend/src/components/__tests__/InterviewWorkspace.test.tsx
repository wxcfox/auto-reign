import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { InterviewWorkspace } from "../InterviewWorkspace";
import {
  createInterviewSession,
  getLastInterviewConfig,
  getModels,
  nextQuestion,
  saveLastInterviewConfig,
  submitAnswer,
  submitFollowUpAnswer,
} from "@/lib/api";

vi.mock("@/lib/api", () => ({
  createInterviewSession: vi.fn(),
  finishInterview: vi.fn(),
  getLastInterviewConfig: vi.fn(),
  getModels: vi.fn(),
  nextQuestion: vi.fn(),
  saveLastInterviewConfig: vi.fn(),
  submitAnswer: vi.fn(),
  submitFollowUpAnswer: vi.fn(),
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
    vi.mocked(getModels).mockResolvedValue({
      providers: [{ provider: "qwen", models: ["qwen3.7-plus"] }],
    });
    vi.mocked(getLastInterviewConfig).mockResolvedValue(baseConfig);
    vi.mocked(saveLastInterviewConfig).mockResolvedValue(baseConfig);
    vi.mocked(submitFollowUpAnswer).mockResolvedValue({
      feedback: "Follow-up feedback",
      missing_points: [],
      weaknesses: [],
      review_suggestions: [],
    });
  });

  it("renders a centered empty chat state with model controls and composer", async () => {
    render(<InterviewWorkspace />);

    expect(await screen.findByText(/Ready when you are/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Model/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Message Auto Reign/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Show advanced settings/i })).toBeInTheDocument();
  });

  it("keeps earlier interview turns visible after advancing to the next question", async () => {
    vi.mocked(createInterviewSession).mockResolvedValue({
      session: baseSession,
      turn: firstTurn,
    });
    vi.mocked(submitAnswer).mockResolvedValue({
      feedback: "Use concrete cache invalidation examples.",
      missing_points: ["Eviction policy"],
      follow_up_question: "How would you handle stale data?",
      weaknesses: ["Needs operational detail"],
      review_suggestions: ["Prepare one production cache incident"],
    });
    vi.mocked(nextQuestion).mockResolvedValue({
      session: { ...baseSession, current_round: 2 },
      turn: secondTurn,
    });

    render(<InterviewWorkspace />);

    fireEvent.click(await screen.findByRole("button", { name: /Show advanced settings/i }));
    fireEvent.change(screen.getByLabelText(/Target company/i), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText(/Target role/i), { target: { value: "Backend Engineer" } });
    fireEvent.click(screen.getByRole("button", { name: /Start interview/i }));

    expect(await screen.findByText(firstTurn.question)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Message Auto Reign/i), {
      target: { value: "I use Redis with TTLs and metrics." },
    });
    fireEvent.click(screen.getByRole("button", { name: /Send answer/i }));

    expect(await screen.findByText(/Use concrete cache invalidation examples/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Next question/i }));

    expect(await screen.findByText(secondTurn.question)).toBeInTheDocument();
    expect(screen.getByText(firstTurn.question)).toBeInTheDocument();
    expect(screen.getByText(/I use Redis with TTLs and metrics/i)).toBeInTheDocument();
    expect(screen.getByText(/Use concrete cache invalidation examples/i)).toBeInTheDocument();
  });

  it("shows an assistant loading state while feedback is being generated", async () => {
    let resolveFeedback: ((value: Awaited<ReturnType<typeof submitAnswer>>) => void) | undefined;
    vi.mocked(createInterviewSession).mockResolvedValue({
      session: baseSession,
      turn: firstTurn,
    });
    vi.mocked(submitAnswer).mockReturnValue(
      new Promise((resolve) => {
        resolveFeedback = resolve;
      }),
    );

    render(<InterviewWorkspace />);

    fireEvent.click(await screen.findByRole("button", { name: /Show advanced settings/i }));
    fireEvent.change(screen.getByLabelText(/Target company/i), { target: { value: "Acme" } });
    fireEvent.change(screen.getByLabelText(/Target role/i), { target: { value: "Backend Engineer" } });
    fireEvent.click(screen.getByRole("button", { name: /Start interview/i }));

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
    });
    await waitFor(() =>
      expect(screen.getByText(/Good structure; add a sharper example/i)).toBeInTheDocument(),
    );
  });
});
