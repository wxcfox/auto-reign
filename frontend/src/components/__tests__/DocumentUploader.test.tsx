import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DocumentUploader } from "../DocumentUploader";

describe("DocumentUploader", () => {
  it("shows markdown and txt upload guidance", () => {
    render(<DocumentUploader onUploaded={() => undefined} />);
    expect(screen.getByText(/Markdown\/TXT/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Upload/i })).toBeDisabled();
  });
});
