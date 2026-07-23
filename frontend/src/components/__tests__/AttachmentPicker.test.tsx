import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AttachmentPicker } from "../AttachmentPicker";
import i18next from "@/i18n/setup";
import { deleteSubtaskContextDraft, uploadSubtaskContext } from "@/lib/api";
import type { SubtaskContextBrief } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  deleteSubtaskContextDraft: vi.fn(),
  uploadSubtaskContext: vi.fn(),
}));

function context(id: number, name = `${id}.txt`): SubtaskContextBrief {
  return {
    id,
    context_type: "attachment",
    name,
    status: "ready",
    mime_type: "text/plain",
    file_extension: ".txt",
    file_size: 3,
    text_length: 3,
    type_data: {},
  };
}

function Harness({
  initial = [],
  recoveryError = null,
  onRetry = vi.fn(),
}: {
  initial?: SubtaskContextBrief[];
  recoveryError?: string | null;
  onRetry?: () => void;
}) {
  const [value, setValue] = useState(initial);
  return (
    <AttachmentPicker
      disabled={false}
      loading={false}
      onChange={setValue}
      onPendingChange={() => undefined}
      onRetry={onRetry}
      recoveryError={recoveryError}
      value={value}
    />
  );
}

describe("AttachmentPicker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    void i18next.changeLanguage("en");
    vi.mocked(uploadSubtaskContext).mockResolvedValue(context(11, "notes.txt"));
    vi.mocked(deleteSubtaskContextDraft).mockResolvedValue();
  });

  it("uploads a subtask_id=0 Context and removes only that draft", async () => {
    render(<Harness />);
    fireEvent.change(screen.getByLabelText("Choose Context files"), {
      target: { files: [new File(["abc"], "notes.txt", { type: "text/plain" })] },
    });

    expect(await screen.findByText("notes.txt")).toBeInTheDocument();
    expect(uploadSubtaskContext).toHaveBeenCalledWith(expect.objectContaining({ name: "notes.txt" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove notes.txt" }));
    await waitFor(() => expect(deleteSubtaskContextDraft).toHaveBeenCalledWith(11));
    expect(screen.queryByText("notes.txt")).not.toBeInTheDocument();
  });

  it("shows parsing state and blocks an eleventh upload while preserving removal", () => {
    const initial = Array.from({ length: 10 }, (_, index) => ({
      ...context(index + 1),
      status: index === 0 ? "parsing" as const : "ready" as const,
    }));
    render(<Harness initial={initial} />);

    expect(screen.getByRole("button", { name: "Attach files" })).toBeDisabled();
    expect(screen.getByText("Parsing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove 1.txt" })).toBeEnabled();
  });

  it("exposes an explicit recovery retry and reports the empty draft state", () => {
    const retry = vi.fn();
    const view = render(<Harness recoveryError="Draft Contexts could not be recovered." onRetry={retry} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry Context recovery" }));
    expect(retry).toHaveBeenCalledTimes(1);

    view.rerender(<Harness />);
    expect(screen.getByText("No draft Contexts.")).toHaveClass("sr-only");
  });
});
