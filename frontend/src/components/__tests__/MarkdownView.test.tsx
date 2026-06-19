import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownView } from "../MarkdownView";

describe("MarkdownView", () => {
  it("renders markdown headings", () => {
    render(<MarkdownView content="# Report" />);
    expect(screen.getByRole("heading", { name: "Report", level: 1 })).toBeInTheDocument();
  });
});
