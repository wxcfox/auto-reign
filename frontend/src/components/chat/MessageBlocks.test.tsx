import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ChatMessage } from "@/components/ChatMessage";

import { MessageBlocks } from "./MessageBlocks";

const timestamp = "2026-07-22T00:00:00Z";

describe("MessageBlocks", () => {
  it("renders markdown text and an accessible structured tool lifecycle", () => {
    render(
      <MessageBlocks
        blocks={[
          { id: "text-1", type: "text", content: "**answer**", status: "done", timestamp },
          {
            id: "tool-1",
            type: "tool",
            tool_use_id: "call-1",
            tool_name: "knowledge_search",
            tool_input: { query: "weather" },
            tool_output: { count: 2 },
            status: "done",
            timestamp,
          },
        ]}
      />,
    );

    expect(screen.getByText("answer").tagName).toBe("STRONG");
    const summary = screen.getByText(/knowledge_search/);
    expect(summary).toHaveTextContent(/done/i);
    fireEvent.click(summary);
    expect(screen.getByLabelText(/tool input/i)).toHaveTextContent('"query": "weather"');
    expect(screen.getByLabelText(/tool output/i)).toHaveTextContent('"count": 2');
  });

  it("renders pending and error states without inventing output", () => {
    render(
      <MessageBlocks
        blocks={[
          {
            id: "pending",
            type: "tool",
            tool_use_id: "call-pending",
            tool_name: "read_file",
            tool_input: {},
            status: "pending",
            timestamp,
          },
          {
            id: "error",
            type: "tool",
            tool_use_id: "call-error",
            tool_name: "lookup",
            tool_input: {},
            tool_output: "failed",
            status: "error",
            timestamp,
          },
        ]}
      />,
    );
    expect(screen.getByText(/read_file/)).toHaveTextContent(/pending/i);
    expect(screen.getByText(/lookup/)).toHaveTextContent(/error/i);
    expect(screen.getAllByText(/tool output/i)).toHaveLength(1);
  });

  it("uses escaped text for unknown blocks and never injects markup", () => {
    const attack = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
    const { container } = render(
      <MessageBlocks
        blocks={[
          { id: "unknown", type: "future", content: attack },
          { id: "cyclic", type: "tool", tool_name: "unsafe", status: "done", tool_input: {} },
        ]}
      />,
    );

    expect(screen.getByText(attack)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.innerHTML).not.toContain("dangerouslySetInnerHTML");
  });

  it("lets ChatMessage render blocks as its structured body", () => {
    render(
      <ChatMessage
        blocks={[
          { id: "text", type: "text", content: "block answer", status: "done", timestamp },
        ]}
        meta="Assistant"
      >
        legacy child
      </ChatMessage>,
    );
    expect(screen.getByText("block answer")).toBeInTheDocument();
    expect(screen.queryByText("legacy child")).toBeNull();
  });
});
