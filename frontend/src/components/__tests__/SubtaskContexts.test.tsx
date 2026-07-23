import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SubtaskContexts } from "../SubtaskContexts";
import i18next from "@/i18n/setup";
import { readSubtaskContextContent } from "@/lib/api";
import type { SubtaskContextBrief } from "@/lib/types";

vi.mock("@/lib/api", () => ({ readSubtaskContextContent: vi.fn() }));

const attachment: SubtaskContextBrief = {
  id: 1,
  context_type: "attachment",
  name: "source.pdf",
  status: "ready",
  mime_type: "application/pdf",
  file_extension: ".pdf",
  file_size: 42,
  text_length: 12,
  type_data: {},
};

describe("SubtaskContexts", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    void i18next.changeLanguage("en");
    vi.mocked(readSubtaskContextContent).mockResolvedValue(new Blob(["pdf"]));
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:test") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
  });

  it("renders attachment metadata, parsed availability, and Knowledge snapshots", () => {
    render(
      <SubtaskContexts contexts={[
        attachment,
        {
          ...attachment,
          id: 2,
          context_type: "knowledge_base",
          name: "Company KB",
          mime_type: null,
          file_extension: null,
          file_size: null,
          type_data: { knowledge_id: "knowledge-1" },
        },
        {
          ...attachment,
          id: 3,
          context_type: "selected_documents",
          name: "Selected sources",
          text_length: 0,
          type_data: { knowledge_id: "knowledge-1", document_ids: ["d1", "d2"] },
        },
      ]} />,
    );

    expect(screen.getByText("source.pdf")).toBeInTheDocument();
    expect(screen.getByText(/42 bytes.*12 parsed characters/)).toBeInTheDocument();
    expect(screen.getByText("Company KB")).toBeInTheDocument();
    expect(screen.getByText("Selected sources")).toBeInTheDocument();
    fireEvent.click(screen.getAllByText("Knowledge selection")[0]!);
    expect(screen.getAllByText(/knowledge-1/)).toHaveLength(2);
    expect(screen.getByText(/No parsed text/)).toBeInTheDocument();
  });

  it("downloads attachment bytes through the content-disposition endpoint and revokes the URL", async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    render(<SubtaskContexts contexts={[attachment]} />);

    fireEvent.click(screen.getByRole("button", { name: "Download source.pdf" }));
    await waitFor(() => expect(readSubtaskContextContent).toHaveBeenCalledWith(1, "attachment"));
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:test"));
    expect(click).toHaveBeenCalledTimes(1);
    click.mockRestore();
  });

  it("disables byte actions for failed parsing without attempting a read", () => {
    render(<SubtaskContexts contexts={[{ ...attachment, status: "failed" }]} />);
    expect(screen.getByRole("button", { name: "Preview source.pdf" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download source.pdf" })).toBeDisabled();
    expect(readSubtaskContextContent).not.toHaveBeenCalled();
  });
});
