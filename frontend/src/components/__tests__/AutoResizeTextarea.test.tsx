import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import { AutoResizeTextarea } from "../AutoResizeTextarea";


function ControlledTextarea({ onKeyDown = vi.fn() }) {
  const [value, setValue] = useState("");
  return (
    <AutoResizeTextarea
      aria-label="Message"
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={onKeyDown}
      value={value}
    />
  );
}


describe("AutoResizeTextarea", () => {
  it("grows with multiline content and resets when cleared", () => {
    render(<ControlledTextarea />);
    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    let measuredHeight = 72;
    Object.defineProperty(textarea, "scrollHeight", {
      configurable: true,
      get: () => measuredHeight,
    });

    fireEvent.change(textarea, { target: { value: "line one\nline two\nline three" } });
    expect(textarea.style.height).toBe("72px");
    expect(textarea.style.overflowY).toBe("hidden");

    measuredHeight = 32;
    fireEvent.change(textarea, { target: { value: "" } });
    expect(textarea.style.height).toBe("32px");
  });

  it("caps height and enables internal scrolling", () => {
    render(<ControlledTextarea />);
    const textarea = screen.getByLabelText("Message") as HTMLTextAreaElement;
    Object.defineProperty(textarea, "scrollHeight", {
      configurable: true,
      value: 260,
    });

    fireEvent.change(textarea, { target: { value: "many\nlines" } });

    expect(textarea.style.height).toBe("180px");
    expect(textarea.style.overflowY).toBe("auto");
  });
});
