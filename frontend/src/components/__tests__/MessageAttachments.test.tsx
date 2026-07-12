import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MessageAttachments } from "../MessageAttachments";
import i18next from "@/i18n/setup";
import { readAttachmentContent } from "@/lib/api";
import type { Attachment } from "@/lib/types";

vi.mock("@/lib/api", () => ({ readAttachmentContent: vi.fn() }));

const attachment: Attachment = {
  id: "attachment-1",
  filename: "diagram.png",
  mime_type: "image/png",
  size_bytes: 4,
  message_id: "message-1",
  created_at: "2026-07-13T00:00:00Z",
};

describe("MessageAttachments", () => {
  const previewWindow = {
    close: vi.fn(),
    location: { replace: vi.fn() },
    opener: {} as unknown,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    i18next.changeLanguage("en");
    vi.spyOn(window, "open").mockReturnValue(previewWindow as unknown as Window);
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:attachment-1"),
      revokeObjectURL: vi.fn(),
    });
    vi.mocked(readAttachmentContent).mockResolvedValue(new Blob(["data"], { type: "image/png" }));
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("previews through an authenticated blob and revokes the object URL", async () => {
    const revokeState: { callback: (() => void) | null } = { callback: null };
    const nativeSetTimeout = window.setTimeout.bind(window);
    vi.spyOn(window, "setTimeout").mockImplementation((handler, timeout) => {
      if (timeout === 60_000) {
        revokeState.callback = handler as () => void;
        return 1;
      }
      return nativeSetTimeout(handler, timeout);
    });
    render(<MessageAttachments attachments={[attachment]} />);

    fireEvent.click(screen.getByRole("button", { name: /preview diagram.png/i }));

    await waitFor(() =>
      expect(readAttachmentContent).toHaveBeenCalledWith("attachment-1", "inline"),
    );
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(window.open).toHaveBeenCalledWith("about:blank", "_blank");
    expect(previewWindow.opener).toBeNull();
    expect(previewWindow.location.replace).toHaveBeenCalledWith("blob:attachment-1");
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    expect(revokeState.callback).not.toBeNull();
    revokeState.callback?.();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:attachment-1");
  });

  it("downloads with the public filename and never exposes a knowledge action", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    render(<MessageAttachments attachments={[attachment]} />);

    expect(screen.queryByText(/knowledge|资料库/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /download diagram.png/i }));

    await waitFor(() =>
      expect(readAttachmentContent).toHaveBeenCalledWith("attachment-1", "attachment"),
    );
    expect(click).toHaveBeenCalledTimes(1);
    await waitFor(() =>
      expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:attachment-1"),
    );
  });

  it("does not create or open a blob after the message unmounts", async () => {
    let resolveRead!: (value: Blob) => void;
    const read = new Promise<Blob>((resolve) => {
      resolveRead = resolve;
    });
    vi.mocked(readAttachmentContent).mockReturnValue(read);
    const view = render(<MessageAttachments attachments={[attachment]} />);

    fireEvent.click(screen.getByRole("button", { name: /preview diagram.png/i }));
    await waitFor(() => expect(readAttachmentContent).toHaveBeenCalledTimes(1));
    view.unmount();
    resolveRead(new Blob(["late"], { type: "image/png" }));
    await read;
    await Promise.resolve();

    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(previewWindow.location.replace).not.toHaveBeenCalled();
    expect(previewWindow.close).toHaveBeenCalledTimes(1);
  });

  it("does not open a stale blob after the attachment projection changes", async () => {
    let resolveRead!: (value: Blob) => void;
    const read = new Promise<Blob>((resolve) => {
      resolveRead = resolve;
    });
    vi.mocked(readAttachmentContent).mockReturnValue(read);
    const view = render(<MessageAttachments attachments={[attachment]} />);
    fireEvent.click(screen.getByRole("button", { name: /preview diagram.png/i }));
    await waitFor(() => expect(readAttachmentContent).toHaveBeenCalledTimes(1));

    view.rerender(
      <MessageAttachments
        attachments={[{ ...attachment, id: "attachment-2", filename: "new.png" }]}
      />,
    );
    resolveRead(new Blob(["stale"], { type: "image/png" }));
    await read;
    await Promise.resolve();

    expect(URL.createObjectURL).not.toHaveBeenCalled();
    expect(previewWindow.location.replace).not.toHaveBeenCalled();
    expect(previewWindow.close).toHaveBeenCalledTimes(1);
  });

  it("does not show a stale read failure on a newer attachment projection", async () => {
    let rejectRead!: (reason: unknown) => void;
    const read = new Promise<Blob>((_resolve, reject) => {
      rejectRead = reject;
    });
    vi.mocked(readAttachmentContent).mockReturnValue(read);
    const view = render(<MessageAttachments attachments={[attachment]} />);
    fireEvent.click(screen.getByRole("button", { name: /preview diagram.png/i }));
    await waitFor(() => expect(readAttachmentContent).toHaveBeenCalledTimes(1));

    view.rerender(
      <MessageAttachments
        attachments={[{ ...attachment, id: "attachment-2", filename: "new.png" }]}
      />,
    );
    rejectRead(new Error("stale object store failure"));
    await read.catch(() => undefined);
    await Promise.resolve();

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(previewWindow.close).toHaveBeenCalledTimes(1);
  });
});
