import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api-error";
import { recordLearningNoteStream } from "@/lib/api";

function streamResponse(body: string) {
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode(body));
        controller.close();
      },
    }),
    {
      headers: { "Content-Type": "text/event-stream" },
      status: 200,
    },
  );
}

describe("apiStream", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("preserves structured SSE error codes as ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        streamResponse(
          'event: error\ndata: {"code":"provider_invalid_response","message":"Bad model response","status_code":502}\n\n',
        ),
      ),
    );

    await expect(
      recordLearningNoteStream(
        { text: "Redis", language: "en" },
        { onDelta: vi.fn() },
      ),
    ).rejects.toMatchObject({
      code: "provider_invalid_response",
      message: "Bad model response",
      name: "ApiError",
      status: 502,
    } satisfies Partial<ApiError>);
  });
});
