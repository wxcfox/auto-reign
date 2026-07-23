import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api-error";
import {
  createAdminUser,
  createAgent,
  createGlobalAgent,
  createKnowledgeCollection,
  createWorkspace,
  createWorkspaceFile,
  deleteAgent,
  deleteKnowledgeCollection,
  deleteKnowledgeDocument,
  deleteSubtaskContextDraft,
  deleteTask,
  deleteWorkspace,
  deleteWorkspaceFile,
  getAgent,
  getTask,
  getWorkspace,
  listAdminUsers,
  listAgents,
  listKnowledgeCollections,
  listKnowledgeDocuments,
  listSubtaskContextDrafts,
  listTasks,
  listWorkspaceFiles,
  listWorkspaces,
  readSubtaskContextContent,
  readWorkspaceFile,
  reindexKnowledgeDocument,
  resetAdminUserPassword,
  renameTask,
  setAdminUserStatus,
  setTaskModel,
  setupAdminPassword,
  updateAgent,
  updateKnowledgeCollection,
  updateWorkspace,
  uploadSubtaskContext,
  uploadKnowledgeDocument,
  writeWorkspaceFile,
} from "@/lib/api";
import { getAuthToken, setAuthToken } from "@/lib/auth";
import type {
  Agent,
  AuthTokenResponse,
  KnowledgeCollection,
  KnowledgeDocument,
  ModelRef,
  ResourceWriteRequest,
  SubtaskContextBrief,
  Workspace,
  WorkspaceConfig,
  WorkspaceScope,
} from "@/lib/types";

const agentFixture: Agent = {
  id: "agent-1",
  name: "Growth helper",
  scope: "global",
  can_manage: true,
  is_active: true,
  config: {
    system_prompt: "Answer clearly.",
    default_model: null,
    home_workspace_id: null,
    knowledge_scopes: [],
  },
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const workspaceFixture: Workspace = {
  id: "workspace-1",
  name: "Growth home",
  scope: "private",
  can_manage: true,
  is_active: true,
  config: { workspace_type: "agent_home", initial_agents_md: "# Rules" },
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const knowledgeCollectionFixture: KnowledgeCollection = {
  id: "collection-1",
  name: "My manuals",
  scope: "private",
  can_manage: true,
  config: {
    retriever_type: "elasticsearch",
    retrieval_mode: "hybrid",
    chunk_size: 900,
    chunk_overlap: 120,
    top_k: 8,
    score_threshold: 0.2,
    vector_weight: 0.7,
    keyword_weight: 0.3,
  },
  is_active: true,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const knowledgeDocumentFixture: KnowledgeDocument = {
  id: "doc-1",
  collection_id: knowledgeCollectionFixture.id,
  name: "guide.md",
  mime_type: "text/markdown",
  size_bytes: 7,
  status: "uploaded",
  index_generation: 0,
  error_code: null,
  error_message: null,
  is_active: true,
  indexed_at: null,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

describe("core API contracts", () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.replaceState(null, "", "/");
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("lists visible agents by default and adds the bearer token", async () => {
    setAuthToken("token-1");
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ agents: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await listAgents();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/agents?scope=visible");
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer token-1");
  });

  it("supports owned and global agent scopes", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => Response.json({ agents: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await listAgents("owned");
    await listAgents("global");

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/agents?scope=owned",
      "/api/agents?scope=global",
    ]);
  });

  it("adds include_inactive only when management callers request it", async () => {
    const fetchMock = vi.fn().mockImplementation(async (path: string) => {
      if (path.startsWith("/api/agents")) {
        return Response.json({ agents: [] });
      }
      if (path.startsWith("/api/workspaces")) {
        return Response.json({ workspaces: [] });
      }
      return Response.json({ collections: [] });
    });
    vi.stubGlobal("fetch", fetchMock);

    await listAgents("owned", { includeInactive: true });
    await listWorkspaces("global", { includeInactive: true });
    await listKnowledgeCollections("owned", { includeInactive: true });
    await listAgents("visible", { includeInactive: false });

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/agents?scope=owned&include_inactive=true",
      "/api/workspaces?scope=global&include_inactive=true",
      "/api/knowledge-collections?scope=owned&include_inactive=true",
      "/api/agents?scope=visible",
    ]);
  });

  it("keeps visible, owned, and global workspace list scopes distinct", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => Response.json({ workspaces: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await listWorkspaces("visible");
    await listWorkspaces("owned");
    await listWorkspaces("global");

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/workspaces?scope=visible",
      "/api/workspaces?scope=owned",
      "/api/workspaces?scope=global",
    ]);
  });

  it("gets visible workspace definitions only through the ordinary typed route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json(workspaceFixture));
    vi.stubGlobal("fetch", fetchMock);

    await getWorkspace("workspace/with space");

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/workspaces/workspace%2Fwith%20space",
    );
    expect(fetchMock.mock.calls[0][0]).not.toContain("/api/admin/");
  });

  it("routes workspace mutations and file operations by authority scope", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const createPayload = {
      name: "Growth home",
      config: { workspace_type: "agent_home", initial_agents_md: "# Rules" },
      user_id: 99,
      scope: "global",
      visibility: "must-not-cross-the-api-boundary",
    } as ResourceWriteRequest<WorkspaceConfig> & {
      scope: string;
      user_id: number;
      visibility: string;
    };

    for (const scope of ["private", "global"] satisfies WorkspaceScope[]) {
      fetchMock.mockReset();
      fetchMock
        .mockResolvedValueOnce(Response.json({ id: "ws-1" }, { status: 201 }))
        .mockResolvedValueOnce(Response.json({ id: "ws-1" }))
        .mockResolvedValueOnce(Response.json({ id: "ws-1", status: "deleted" }))
        .mockResolvedValueOnce(Response.json({ directory: "", items: [] }))
        .mockResolvedValueOnce(Response.json({ path: "AGENTS.md", content: "# Rules" }))
        .mockResolvedValueOnce(Response.json({ path: "notes/a.md", content: "A" }))
        .mockResolvedValueOnce(Response.json({ path: "AGENTS.md", content: "# New" }))
        .mockResolvedValueOnce(new Response(null, { status: 204 }));

      await createWorkspace(scope, createPayload);
      await updateWorkspace(scope, "ws-1", { ...createPayload, is_active: false });
      await deleteWorkspace(scope, "ws-1");
      await listWorkspaceFiles(scope, "ws-1", "");
      await readWorkspaceFile(scope, "ws-1", "AGENTS.md");
      await createWorkspaceFile(scope, "ws-1", { path: "notes/a.md", content: "A" });
      await writeWorkspaceFile(scope, "ws-1", {
        path: "AGENTS.md",
        content: "# New",
        expected_etag: "etag-1",
      });
      await deleteWorkspaceFile(scope, "ws-1", "notes/a.md");

      const base = scope === "global" ? "/api/admin/workspaces" : "/api/workspaces";
      expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
        base,
        `${base}/ws-1`,
        `${base}/ws-1`,
        `${base}/ws-1/files?directory=`,
        `${base}/ws-1/files/content?path=AGENTS.md`,
        `${base}/ws-1/files/content`,
        `${base}/ws-1/files/content`,
        `${base}/ws-1/files?path=notes%2Fa.md`,
      ]);
      for (const callIndex of [0, 1]) {
        const body = JSON.parse(fetchMock.mock.calls[callIndex][1].body as string);
        expect(body).not.toHaveProperty("user_id");
        expect(body).not.toHaveProperty("scope");
        expect(body).not.toHaveProperty("visibility");
      }
    }
  });

  it("exposes complete agent CRUD clients with encoded ids and projected payloads", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(agentFixture, { status: 201 }))
      .mockResolvedValueOnce(Response.json(agentFixture, { status: 201 }))
      .mockResolvedValueOnce(Response.json(agentFixture))
      .mockResolvedValueOnce(Response.json(agentFixture))
      .mockResolvedValueOnce(Response.json({ id: agentFixture.id, status: "deleted" }));
    vi.stubGlobal("fetch", fetchMock);
    const payload = {
      name: agentFixture.name,
      config: agentFixture.config,
      user_id: 7,
      scope: "private",
      token_version: 11,
      visibility: "private",
    } as ResourceWriteRequest<Agent["config"]> & {
      user_id: number;
      scope: string;
      token_version: number;
      visibility: string;
    };

    await createAgent(payload);
    await createGlobalAgent(payload);
    await getAgent("agent/with space");
    await updateAgent("agent/with space", { ...payload, is_active: false });
    await deleteAgent("agent/with space");

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/agents",
      "/api/admin/agents",
      "/api/agents/agent%2Fwith%20space",
      "/api/agents/agent%2Fwith%20space",
      "/api/agents/agent%2Fwith%20space",
    ]);
    expect(fetchMock.mock.calls.map(([, init]) => init.method)).toEqual([
      "POST",
      "POST",
      undefined,
      "PUT",
      "DELETE",
    ]);
    for (const callIndex of [0, 1, 3]) {
      const body = JSON.parse(fetchMock.mock.calls[callIndex][1].body as string);
      expect(body).not.toHaveProperty("user_id");
      expect(body).not.toHaveProperty("scope");
      expect(body).not.toHaveProperty("token_version");
      expect(body).not.toHaveProperty("visibility");
    }
  });

  it("uses ordinary typed list endpoints for every knowledge list scope", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(async () => Response.json({ collections: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await listKnowledgeCollections("visible");
    await listKnowledgeCollections("owned");
    await listKnowledgeCollections("global");

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/knowledge-collections?scope=visible",
      "/api/knowledge-collections?scope=owned",
      "/api/knowledge-collections?scope=global",
    ]);
    expect(fetchMock.mock.calls.every(([path]) => !String(path).includes("/api/admin/"))).toBe(
      true,
    );
  });

  it("routes collection mutations separately and projects complete config", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(async () => Response.json(knowledgeCollectionFixture));
    vi.stubGlobal("fetch", fetchMock);
    const payload = {
      name: knowledgeCollectionFixture.name,
      config: knowledgeCollectionFixture.config,
      user_id: 2,
      scope: "global",
      visibility: "global",
    } as ResourceWriteRequest<KnowledgeCollection["config"]> & {
      user_id: number;
      scope: string;
      visibility: string;
    };

    await createKnowledgeCollection("private", payload);
    await createKnowledgeCollection("global", payload);
    await updateKnowledgeCollection("private", "collection/one", {
      ...payload,
      is_active: false,
    });
    await updateKnowledgeCollection("global", "collection/one", {
      ...payload,
      is_active: true,
    });
    await deleteKnowledgeCollection("private", "collection/one");
    await deleteKnowledgeCollection("global", "collection/one");

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/knowledge-collections",
      "/api/admin/knowledge-collections",
      "/api/knowledge-collections/collection%2Fone",
      "/api/admin/knowledge-collections/collection%2Fone",
      "/api/knowledge-collections/collection%2Fone",
      "/api/admin/knowledge-collections/collection%2Fone",
    ]);
    for (const callIndex of [0, 1, 2, 3]) {
      const body = JSON.parse(fetchMock.mock.calls[callIndex][1].body as string);
      expect(body.config).toEqual(knowledgeCollectionFixture.config);
      expect(body).not.toHaveProperty("user_id");
      expect(body).not.toHaveProperty("scope");
      expect(body).not.toHaveProperty("visibility");
    }
  });

  it("uses typed admin user routes and projects payloads", async () => {
    const user = {
      id: 7,
      username: "reader",
      display_name: "Reader",
      role: "user" as const,
      is_active: true,
      created_at: "2026-07-13T00:00:00Z",
      updated_at: "2026-07-13T00:00:00Z",
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ users: [user] }))
      .mockResolvedValueOnce(Response.json(user, { status: 201 }))
      .mockResolvedValueOnce(Response.json({ ...user, is_active: false }))
      .mockResolvedValueOnce(Response.json(user));
    vi.stubGlobal("fetch", fetchMock);

    await listAdminUsers();
    await createAdminUser({
      username: user.username,
      display_name: user.display_name,
      password: "correct horse battery staple",
      role: "admin",
      token_version: 42,
      user_id: 99,
    } as Parameters<typeof createAdminUser>[0] & {
      role: string;
      token_version: number;
      user_id: number;
    });
    await setAdminUserStatus(7, false);
    await resetAdminUserPassword(7, "new correct horse battery staple");

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/admin/users",
      "/api/admin/users",
      "/api/admin/users/7/status",
      "/api/admin/users/7/reset-password",
    ]);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body as string)).toEqual({
      username: "reader",
      display_name: "Reader",
      password: "correct horse battery staple",
    });
    expect(fetchMock.mock.calls[2][1]).toEqual(
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ is_active: false }) }),
    );
    expect(fetchMock.mock.calls[3][1]).toEqual(
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ password: "new correct horse battery staple" }),
      }),
    );
  });

  it("uploads a document to the selected collection without forcing content-type", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json(knowledgeDocumentFixture));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["# Guide"], "guide.md", { type: "text/markdown" });

    await uploadKnowledgeDocument("collection-1", file);

    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/knowledge-collections/collection-1/documents");
    expect(init.body).toBeInstanceOf(FormData);
    expect(new Headers(init.headers).has("Content-Type")).toBe(false);
  });

  it("posts reindex to the document resource", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      Response.json({ ...knowledgeDocumentFixture, status: "queued" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await reindexKnowledgeDocument("collection-1", "doc-1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/knowledge-collections/collection-1/documents/doc-1/reindex",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("creates a private collection through the private domain endpoint with JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json(knowledgeCollectionFixture));
    vi.stubGlobal("fetch", fetchMock);

    await createKnowledgeCollection("private", {
      name: "My manuals",
      config: knowledgeCollectionFixture.config,
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/knowledge-collections",
      expect.objectContaining({ method: "POST" }),
    );
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
  });

  it("lists inactive documents only for the manager view", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json({ documents: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await listKnowledgeDocuments("collection-1", { includeInactive: true });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/knowledge-collections/collection-1/documents?include_inactive=true",
      expect.anything(),
    );
  });

  it("returns cleanup_pending for a retryable delete", async () => {
    const pending = { document_id: "doc-1", status: "cleanup_pending" as const };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(pending, { status: 202 })));

    await expect(deleteKnowledgeDocument("collection-1", "doc-1")).resolves.toEqual(pending);
  });

  it("returns null for a fully cleaned 204 response without parsing a body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    await expect(deleteKnowledgeDocument("collection-1", "doc-1")).resolves.toBeNull();
  });

  it("sets up the initial administrator password through the one-time endpoint", async () => {
    const response: AuthTokenResponse = {
      access_token: "admin-token",
      token_type: "bearer",
      user: {
        id: 1,
        username: "admin",
        display_name: "Admin",
        role: "admin",
        is_active: true,
        created_at: "2026-07-13T00:00:00Z",
        updated_at: "2026-07-13T00:00:00Z",
      },
    };
    const fetchMock = vi.fn().mockResolvedValue(Response.json(response));
    vi.stubGlobal("fetch", fetchMock);

    await expect(setupAdminPassword("correct horse battery staple")).resolves.toEqual(response);

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/auth/admin-password/setup",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock.mock.calls[0][1].body).toBe(
      JSON.stringify({ password: "correct horse battery staple" }),
    );
  });

  it("uses the Task endpoints for list, detail, rename, and 204 delete", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json({ tasks: [] }))
      .mockResolvedValueOnce(Response.json({ id: 7 }))
      .mockResolvedValueOnce(Response.json({ id: 7, name: "Renamed" }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await listTasks();
    await getTask(7);
    await renameTask(7, "Renamed");
    await expect(deleteTask(7)).resolves.toBeUndefined();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/tasks");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/tasks/7");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/tasks/7");
    expect(fetchMock.mock.calls[2][1]).toEqual(
      expect.objectContaining({ method: "PATCH", body: JSON.stringify({ name: "Renamed" }) }),
    );
    expect(fetchMock.mock.calls[3][0]).toBe("/api/tasks/7");
    expect(fetchMock.mock.calls[3][1]).toEqual(expect.objectContaining({ method: "DELETE" }));
  });

  it("sets and clears a Task model override with the exact body", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(Response.json({ id: 7 }))
      .mockResolvedValueOnce(Response.json({ id: 7 }));
    vi.stubGlobal("fetch", fetchMock);
    const modelOverride: ModelRef = {
      provider: "local-runtime",
      model: "custom-chat",
    };

    await setTaskModel(7, modelOverride);
    await setTaskModel(7, null);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/tasks/7/model");
    expect(fetchMock.mock.calls[0][1]).toEqual(
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ model_override: modelOverride }),
      }),
    );
    expect(fetchMock.mock.calls[1][1]).toEqual(
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ model_override: null }),
      }),
    );
  });

  it("clears stale tokens and redirects private pages on unauthorized responses", async () => {
    setAuthToken("token-1");
    window.history.replaceState(null, "", "/agents");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          { detail: { code: "auth_required", message: "Authentication required." } },
          { status: 401 },
        ),
      ),
    );

    await expect(listAgents()).rejects.toMatchObject({
      code: "auth_required",
      status: 401,
    } satisfies Partial<ApiError>);

    expect(getAuthToken()).toBeNull();
    expect(window.location.pathname).toBe("/login");
  });

  it("uses authenticated SubtaskContext endpoints and preserves blob response headers", async () => {
    setAuthToken("token-context");
    const context: SubtaskContextBrief = {
      id: 11,
      context_type: "attachment",
      name: "notes.txt",
      status: "ready",
      mime_type: "text/plain",
      file_extension: ".txt",
      file_size: 3,
      text_length: 3,
      type_data: {},
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(Response.json(context, { status: 201 }))
      .mockResolvedValueOnce(Response.json({ items: [context] }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(
        new Response("abc", {
          status: 200,
          headers: {
            "Content-Type": "text/plain",
            "Content-Disposition": "attachment; filename=notes.txt",
          },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      uploadSubtaskContext(new File(["abc"], "notes.txt", { type: "text/plain" })),
    ).resolves.toEqual(context);
    await expect(listSubtaskContextDrafts()).resolves.toEqual([context]);
    await expect(deleteSubtaskContextDraft(11)).resolves.toBeUndefined();
    const content = await readSubtaskContextContent(11, "attachment");
    expect(content.size).toBe(3);
    expect(content.type).toMatch(/^text\/plain(?:;|$)/);

    const uploadInit = fetchMock.mock.calls[0][1] as RequestInit;
    expect(uploadInit.body).toBeInstanceOf(FormData);
    expect((uploadInit.headers as Headers).get("Content-Type")).toBeNull();
    for (const call of fetchMock.mock.calls) {
      expect((call[1].headers as Headers).get("Authorization")).toBe(
        "Bearer token-context",
      );
    }
    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/api/subtask-contexts/attachments",
      "/api/subtask-contexts/drafts",
      "/api/subtask-contexts/11",
      "/api/subtask-contexts/11/content?disposition=attachment",
    ]);
  });

  it("keeps a newer identity across late context upload, delete, and blob 401 responses", async () => {
    window.history.replaceState(null, "", "/chat");
    const operations: Array<() => Promise<unknown>> = [
      () => uploadSubtaskContext(new File(["abc"], "notes.txt", { type: "text/plain" })),
      () => deleteSubtaskContextDraft(11),
      () => readSubtaskContextContent(11, "inline"),
    ];

    for (const [index, operation] of operations.entries()) {
      const staleToken = `stale-attachment-token-${index}`;
      const freshToken = `fresh-attachment-token-${index}`;
      setAuthToken(staleToken);
      let resolveFetch: ((response: Response) => void) | undefined;
      const fetchMock = vi.fn().mockReturnValue(
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
      );
      vi.stubGlobal("fetch", fetchMock);

      const request = operation();
      expect((fetchMock.mock.calls[0][1].headers as Headers).get("Authorization")).toBe(
        `Bearer ${staleToken}`,
      );
      setAuthToken(freshToken);
      resolveFetch?.(
        Response.json(
          { detail: { code: "token_invalid", message: "Token is invalid." } },
          { status: 401 },
        ),
      );

      await expect(request).rejects.toMatchObject({ code: "token_invalid", status: 401 });
      expect(getAuthToken()).toBe(freshToken);
      expect(window.location.pathname).toBe("/chat");
    }
  });

  it("does not let a late unauthorized response clear a newer token", async () => {
    setAuthToken("stale-token");
    window.history.replaceState(null, "", "/agents");
    let resolveFetch: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const request = listAgents();
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe("Bearer stale-token");
    setAuthToken("fresh-token");
    resolveFetch?.(
      Response.json(
        { detail: { code: "token_invalid", message: "Token is invalid." } },
        { status: 401 },
      ),
    );

    await expect(request).rejects.toMatchObject({
      code: "token_invalid",
      status: 401,
    } satisfies Partial<ApiError>);
    expect(getAuthToken()).toBe("fresh-token");
    expect(window.location.pathname).toBe("/agents");
  });

  it("redirects when the request token expires before its unauthorized response", async () => {
    const startedAt = Date.UTC(2026, 6, 13, 12, 0, 0);
    const expiresAt = Math.floor(startedAt / 1000) + 60;
    const token = jwtWithExpiration(expiresAt);
    vi.spyOn(Date, "now").mockReturnValue(startedAt);
    setAuthToken(token);
    window.history.replaceState(null, "", "/agents");
    let resolveFetch: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const request = listAgents();
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBe(`Bearer ${token}`);
    vi.mocked(Date.now).mockReturnValue((expiresAt + 1) * 1000);
    resolveFetch?.(
      Response.json(
        { detail: { code: "token_expired", message: "Token is expired." } },
        { status: 401 },
      ),
    );

    await expect(request).rejects.toMatchObject({
      code: "token_expired",
      status: 401,
    } satisfies Partial<ApiError>);
    expect(getAuthToken()).toBeNull();
    expect(window.location.pathname).toBe("/login");
  });

  it("does not let a late anonymous unauthorized response affect a newer token", async () => {
    window.history.replaceState(null, "", "/agents");
    let resolveFetch: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve;
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const request = listAgents();
    const headers = fetchMock.mock.calls[0][1].headers as Headers;
    expect(headers.get("Authorization")).toBeNull();
    setAuthToken("fresh-token");
    resolveFetch?.(
      Response.json(
        { detail: { code: "auth_required", message: "Authentication required." } },
        { status: 401 },
      ),
    );

    await expect(request).rejects.toMatchObject({
      code: "auth_required",
      status: 401,
    } satisfies Partial<ApiError>);
    expect(getAuthToken()).toBe("fresh-token");
    expect(window.location.pathname).toBe("/agents");
  });

  it("does not navigate away from setup on an ordinary unauthorized response", async () => {
    setAuthToken("stale-token");
    window.history.replaceState(null, "", "/setup");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        Response.json(
          { detail: { code: "auth_required", message: "Authentication required." } },
          { status: 401 },
        ),
      ),
    );

    await expect(listAgents()).rejects.toMatchObject({
      code: "auth_required",
      status: 401,
    } satisfies Partial<ApiError>);

    expect(getAuthToken()).toBeNull();
    expect(window.location.pathname).toBe("/setup");
  });

});

function jwtWithExpiration(exp: number) {
  const payload = window
    .btoa(JSON.stringify({ exp }))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replaceAll("=", "");
  return `header.${payload}.signature`;
}
