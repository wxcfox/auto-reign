import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api-error";
import { getWorkspaceStatus, recordLearningNoteStream, uploadMaterials } from "@/lib/api";
import { getAuthToken, setAuthToken } from "@/lib/auth";

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
  beforeEach(() => {
    localStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("adds bearer tokens to JSON requests", async () => {
    setAuthToken("token-1");
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json({
        schema_version: 1,
        language: "zh-CN",
        artifact_count: 0,
        initialized: true,
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await getWorkspaceStatus();

    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token-1");
  });

  it("adds bearer tokens to upload requests", async () => {
    setAuthToken("token-1");
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ sources: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await uploadMaterials([new File(["# Redis"], "redis.md", { type: "text/markdown" })]);

    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token-1");
  });

  it("adds bearer tokens to streaming requests", async () => {
    setAuthToken("token-1");
    const fetchMock = vi.fn().mockResolvedValue(streamResponse('event: result\ndata: {"ok":true}\n\n'));
    vi.stubGlobal("fetch", fetchMock);

    await recordLearningNoteStream(
      { text: "Redis", language: "en" },
      { onDelta: vi.fn() },
    );

    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token-1");
  });

  it("clears stale tokens and redirects private pages on unauthorized responses", async () => {
    setAuthToken("token-1");
    window.history.replaceState(null, "", "/library");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          { detail: { code: "auth_required", message: "Authentication required." } },
          { status: 401 },
        ),
      ),
    );

    await expect(getWorkspaceStatus()).rejects.toMatchObject({
      code: "auth_required",
      status: 401,
    } satisfies Partial<ApiError>);

    expect(getAuthToken()).toBeNull();
    expect(window.location.pathname).toBe("/login");
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
