import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentUploader } from "../DocumentUploader";
import { uploadMaterials } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  uploadMaterials: vi.fn(),
}));

describe("DocumentUploader", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(uploadMaterials).mockResolvedValue({ sources: [] });
  });

  it("opens a compact upload action without showing a native file chooser row", () => {
    const { container } = render(<DocumentUploader onUploaded={() => undefined} />);

    expect(screen.getByRole("button", { name: /Upload/i })).toBeEnabled();
    expect(screen.queryByText(/Markdown\/TXT\/PDF\/DOCX/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Document")).not.toBeInTheDocument();
    expect(container.querySelector('input[type="file"]')).toHaveClass("sr-only");
  });

  it("uploads immediately after files are selected", async () => {
    const onUploaded = vi.fn();
    const { container } = render(<DocumentUploader onUploaded={onUploaded} />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["redis"], "redis.md", { type: "text/markdown" });

    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(uploadMaterials).toHaveBeenCalledWith([file]));
    expect(onUploaded).toHaveBeenCalledWith({ sources: [] });
  });
});
