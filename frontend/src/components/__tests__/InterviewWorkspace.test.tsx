import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { InterviewWorkspace } from "../InterviewWorkspace";

describe("InterviewWorkspace", () => {
  it("renders configuration and answer areas", () => {
    render(<InterviewWorkspace />);
    expect(screen.getByLabelText(/Target company/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/Target role/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Start interview/i })).toBeInTheDocument();
  });
});
