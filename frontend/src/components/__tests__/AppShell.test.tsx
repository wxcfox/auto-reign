import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "../AppShell";

vi.mock("next/navigation", () => ({
  usePathname: () => "/interview",
}));

describe("AppShell", () => {
  it("renders a fixed chat-style sidebar with primary actions and secondary items", () => {
    render(
      <AppShell>
        <div>Current page</div>
      </AppShell>,
    );

    expect(screen.getByRole("link", { name: /New interview/i })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: /Primary/i })).toBeInTheDocument();
    const moreButton = screen.getByRole("button", { name: /More/i });
    expect(moreButton).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(moreButton);
    expect(moreButton).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/Settings/i)).toBeInTheDocument();
  });
});
