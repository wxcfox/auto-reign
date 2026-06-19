import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusPill } from "../StatusPill";

describe("StatusPill", () => {
  it("renders the status label", () => {
    render(<StatusPill label="Indexed" tone="success" />);
    expect(screen.getByText("Indexed")).toBeInTheDocument();
  });
});
