import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AttachmentPicker } from "../AttachmentPicker";
import i18next from "@/i18n/setup";
import { deleteAttachmentDraft, uploadAttachment } from "@/lib/api";
import type { Attachment } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  deleteAttachmentDraft: vi.fn(),
  uploadAttachment: vi.fn(),
}));

function attachment(id: string, filename = `${id}.txt`): Attachment {
  return {
    id,
    filename,
    mime_type: "text/plain",
    size_bytes: 3,
    message_id: null,
    created_at: "2026-07-13T00:00:00Z",
  };
}

function Harness({ initial = [] }: { initial?: Attachment[] }) {
  const [value, setValue] = useState(initial);
  return (
    <AttachmentPicker
      disabled={false}
      loading={false}
      onChange={setValue}
      onPendingChange={vi.fn()}
      onRetry={vi.fn()}
      recoveryError={null}
      value={value}
    />
  );
}

describe("AttachmentPicker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    i18next.changeLanguage("en");
  });

  it("uploads selected files and removes committed drafts", async () => {
    vi.mocked(uploadAttachment).mockResolvedValue(attachment("attachment-1", "notes.txt"));
    vi.mocked(deleteAttachmentDraft).mockResolvedValue();
    render(<Harness />);

    fireEvent.change(screen.getByLabelText(/attach files/i), {
      target: { files: [new File(["abc"], "notes.txt", { type: "text/plain" })] },
    });

    expect(await screen.findByText("notes.txt")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /remove notes.txt/i }));
    await waitFor(() => expect(deleteAttachmentDraft).toHaveBeenCalledWith("attachment-1"));
    expect(screen.queryByText("notes.txt")).not.toBeInTheDocument();
  });

  it("rejects a selection that would exceed ten drafts", async () => {
    render(<Harness initial={Array.from({ length: 10 }, (_, index) => attachment(`a-${index}`))} />);

    expect(screen.getByRole("button", { name: /attach files/i })).toBeDisabled();
    expect(uploadAttachment).not.toHaveBeenCalled();
  });

  it("shows recovery failure and exposes retry", () => {
    const onRetry = vi.fn();
    render(
      <AttachmentPicker
        disabled={false}
        loading={false}
        onChange={vi.fn()}
        onPendingChange={vi.fn()}
        onRetry={onRetry}
        recoveryError="Draft attachments could not be loaded."
        value={[]}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Draft attachments could not be loaded");
    fireEvent.click(screen.getByRole("button", { name: /retry loading attachments/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("announces an empty recovered draft list", () => {
    render(<Harness />);

    expect(screen.getByText("No draft attachments.")).toHaveClass("sr-only");
  });

  it("ignores a late upload result after unmount so recovery remains authoritative", async () => {
    let resolveUpload!: (value: Attachment) => void;
    const upload = new Promise<Attachment>((resolve) => {
      resolveUpload = resolve;
    });
    vi.mocked(uploadAttachment).mockReturnValue(upload);
    const onChange = vi.fn();
    const onPendingChange = vi.fn();
    const view = render(
      <AttachmentPicker
        disabled={false}
        loading={false}
        onChange={onChange}
        onPendingChange={onPendingChange}
        onRetry={vi.fn()}
        recoveryError={null}
        value={[]}
      />,
    );
    fireEvent.change(screen.getByLabelText(/attach files/i), {
      target: { files: [new File(["late"], "late.txt", { type: "text/plain" })] },
    });
    await waitFor(() => expect(uploadAttachment).toHaveBeenCalledTimes(1));

    view.unmount();
    resolveUpload(attachment("attachment-late", "late.txt"));
    await upload;
    await Promise.resolve();

    expect(onChange).not.toHaveBeenCalled();
    expect(onPendingChange).toHaveBeenCalledWith(true);
  });

  it("offers a draft-list retry after an ambiguous upload failure", async () => {
    vi.mocked(uploadAttachment).mockRejectedValue(new Error("response lost"));
    const onRetry = vi.fn();
    render(
      <AttachmentPicker
        disabled={false}
        loading={false}
        onChange={vi.fn()}
        onPendingChange={vi.fn()}
        onRetry={onRetry}
        recoveryError={null}
        value={[]}
      />,
    );

    fireEvent.change(screen.getByLabelText(/attach files/i), {
      target: { files: [new File(["maybe stored"], "uncertain.txt", { type: "text/plain" })] },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be uploaded");
    fireEvent.click(screen.getByRole("button", { name: /retry loading attachments/i }));

    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});
