import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AdminUserManagementPage } from "../AdminUserManagementPage";
import i18next, { namespaces } from "@/i18n/setup";
import {
  createAdminUser,
  listAdminUsers,
  resetAdminUserPassword,
  setAdminUserStatus,
} from "@/lib/api";
import type { AdminUser } from "@/lib/types";

vi.mock("@/lib/api", () => ({
  createAdminUser: vi.fn(),
  listAdminUsers: vi.fn(),
  resetAdminUserPassword: vi.fn(),
  setAdminUserStatus: vi.fn(),
}));

const adminFixture: AdminUser = {
  id: 1,
  username: "admin",
  display_name: "Administrator",
  role: "admin",
  is_active: true,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

const aliceFixture: AdminUser = {
  id: 2,
  username: "alice",
  display_name: "Alice",
  role: "user",
  is_active: true,
  created_at: "2026-07-13T00:00:00Z",
  updated_at: "2026-07-13T00:00:00Z",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

async function openCreateDialog() {
  const trigger = await screen.findByRole("button", { name: "Create user" });
  fireEvent.click(trigger);
  return {
    dialog: screen.getByRole("dialog", { name: "Create user" }),
    trigger,
  };
}

function fillCreateDialog(
  dialog: HTMLElement,
  values: { username?: string; displayName?: string; password?: string } = {},
) {
  fireEvent.change(within(dialog).getByLabelText("Username"), {
    target: { value: values.username ?? "reader" },
  });
  fireEvent.change(within(dialog).getByLabelText("Display name"), {
    target: { value: values.displayName ?? "Reader" },
  });
  fireEvent.change(within(dialog).getByLabelText("Password"), {
    target: { value: values.password ?? "secret-password" },
  });
}

async function openResetDialog(name = aliceFixture.username) {
  const trigger = await screen.findByRole("button", {
    name: `Reset password for ${name}`,
  });
  fireEvent.click(trigger);
  return {
    dialog: screen.getByRole("dialog", { name: `Reset password for ${name}` }),
    trigger,
  };
}

function rowFor(username: string) {
  const row = screen.getByRole("rowheader", { name: username }).closest("tr");
  if (!row) {
    throw new Error(`No table row found for ${username}`);
  }
  return row;
}

describe("AdminUserManagementPage", () => {
  beforeEach(async () => {
    vi.resetAllMocks();
    await i18next.changeLanguage("en");
    vi.mocked(listAdminUsers).mockResolvedValue({
      users: [adminFixture, aliceFixture],
    });
    vi.mocked(createAdminUser).mockResolvedValue(aliceFixture);
    vi.mocked(resetAdminUserPassword).mockResolvedValue(aliceFixture);
    vi.mocked(setAdminUserStatus).mockResolvedValue(aliceFixture);
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a semantic account table and protects the fixed administrator", async () => {
    render(<AdminUserManagementPage />);

    expect(screen.getByRole("status")).toHaveTextContent("Loading users…");
    expect(await screen.findByRole("table", { name: "User accounts" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "admin" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "alice" })).toBeInTheDocument();
    for (const name of ["Username", "Display name", "Status", "Created", "Actions"]) {
      expect(screen.getByRole("columnheader", { name })).toHaveAttribute("scope", "col");
    }
    expect(screen.getByRole("rowheader", { name: "alice" })).toHaveAttribute(
      "scope",
      "row",
    );
    expect(
      within(rowFor("alice")).getByText(
        new Intl.DateTimeFormat("en").format(new Date(aliceFixture.created_at)),
      ),
    ).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "Disable admin" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Reset password for admin" }),
    ).toBeDisabled();
    expect(
      screen.getByText("The fixed administrator cannot be changed here."),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Disable admin" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Reset password for admin" }),
    );
    expect(setAdminUserStatus).not.toHaveBeenCalled();
    expect(resetAdminUserPassword).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Disable alice" })).toBeEnabled();
    expect(screen.queryByRole("combobox", { name: /role/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete|promote/i })).not.toBeInTheDocument();
  });

  it("renders an accessible empty account table", async () => {
    vi.mocked(listAdminUsers).mockResolvedValue({ users: [] });
    render(<AdminUserManagementPage />);

    const table = await screen.findByRole("table", { name: "User accounts" });
    expect(within(table).getByText("No users found.")).toBeInTheDocument();
    expect(within(table).getAllByRole("columnheader")).toHaveLength(5);
  });

  it("shows a stable recoverable load error", async () => {
    vi.mocked(listAdminUsers)
      .mockRejectedValueOnce(new Error("database connection details"))
      .mockResolvedValueOnce({ users: [aliceFixture] });
    render(<AdminUserManagementPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Users could not be loaded.",
    );
    expect(screen.queryByText(/database connection details/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("rowheader", { name: "alice" })).toBeInTheDocument();
    expect(listAdminUsers).toHaveBeenCalledTimes(2);
  });

  it("creates an ordinary user, clears the pending secret, and guards same-tick submit", async () => {
    const pending = deferred<AdminUser>();
    const created = {
      ...aliceFixture,
      id: 3,
      username: "reader",
      display_name: "Reader",
    };
    vi.mocked(createAdminUser).mockReturnValue(pending.promise);
    const storageWrite = vi.spyOn(Storage.prototype, "setItem");
    const consoleSinks = [
      vi.spyOn(console, "log").mockImplementation(() => {}),
      vi.spyOn(console, "warn").mockImplementation(() => {}),
      vi.spyOn(console, "error").mockImplementation(() => {}),
    ];
    render(<AdminUserManagementPage />);
    const { dialog } = await openCreateDialog();
    const secret = "correct horse battery staple";
    fillCreateDialog(dialog, {
      username: "  reader  ",
      displayName: "  Reader  ",
      password: secret,
    });

    const submit = within(dialog).getByRole("button", { name: "Create" });
    fireEvent.click(submit);
    fireEvent.submit(within(dialog).getByRole("form"));

    expect(createAdminUser).toHaveBeenCalledTimes(1);
    expect(createAdminUser).toHaveBeenCalledWith({
      username: "reader",
      display_name: "Reader",
      password: secret,
    });
    expect(within(dialog).getByLabelText("Password")).toHaveValue("");
    expect(screen.queryByDisplayValue(secret)).not.toBeInTheDocument();
    expect(JSON.stringify(storageWrite.mock.calls)).not.toContain(secret);
    for (const sink of consoleSinks) {
      expect(JSON.stringify(sink.mock.calls)).not.toContain(secret);
    }
    expect(window.location.href).not.toContain(secret);
    expect(window.location.href).not.toContain(encodeURIComponent(secret));
    expect(submit).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.queryByRole("combobox", { name: /role/i })).not.toBeInTheDocument();

    pending.resolve(created);
    expect(await screen.findByRole("rowheader", { name: "reader" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each([
    {
      label: "minimum lengths without trimming the password",
      username: "abc",
      displayName: "   ",
      password: "      ",
      expectedDisplayName: "",
    },
    {
      label: "maximum Unicode code-point lengths",
      username: "a".repeat(80),
      displayName: ` ${"😀".repeat(120)} `,
      password: "😀".repeat(256),
      expectedDisplayName: "😀".repeat(120),
    },
  ])("accepts $label", async ({ username, displayName, password, expectedDisplayName }) => {
    vi.mocked(createAdminUser).mockResolvedValue({
      ...aliceFixture,
      username,
      display_name: expectedDisplayName,
    });
    render(<AdminUserManagementPage />);
    const { dialog } = await openCreateDialog();
    fillCreateDialog(dialog, { username: ` ${username} `, displayName, password });

    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    await waitFor(() => expect(createAdminUser).toHaveBeenCalledTimes(1));
    expect(createAdminUser).toHaveBeenCalledWith({
      username,
      display_name: expectedDisplayName,
      password,
    });
  });

  it.each([
    {
      label: "short username",
      username: "ab",
      displayName: "Reader",
      password: "valid-password",
      error: "Username must contain 3 to 80 characters.",
    },
    {
      label: "long username",
      username: "a".repeat(81),
      displayName: "Reader",
      password: "valid-password",
      error: "Username must contain 3 to 80 characters.",
    },
    {
      label: "non-ASCII username",
      username: "用户name",
      displayName: "Reader",
      password: "valid-password",
      error:
        "Username may contain only ASCII letters, numbers, periods, underscores, and hyphens.",
    },
    {
      label: "long display name by code point",
      username: "reader",
      displayName: "😀".repeat(121),
      password: "valid-password",
      error: "Display name must contain no more than 120 characters.",
    },
    {
      label: "short password",
      username: "reader",
      displayName: "Reader",
      password: "12345",
      error: "Password must contain 6 to 256 characters.",
    },
    {
      label: "long password by code point",
      username: "reader",
      displayName: "Reader",
      password: "😀".repeat(257),
      error: "Password must contain 6 to 256 characters.",
    },
  ])("rejects $label deterministically and clears the secret", async (values) => {
    render(<AdminUserManagementPage />);
    const { dialog } = await openCreateDialog();
    fillCreateDialog(dialog, values);

    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(values.error);
    expect(createAdminUser).not.toHaveBeenCalled();
    expect(within(dialog).getByLabelText("Password")).toHaveValue("");
    expect(within(dialog).getByLabelText("Username")).toHaveValue(values.username);
    expect(within(dialog).getByLabelText("Display name")).toHaveValue(
      values.displayName,
    );
  });

  it("keeps non-secret create draft after failure and retries with a new secret", async () => {
    const firstSecret = "first-secret";
    const secondSecret = "second-secret";
    vi.mocked(createAdminUser)
      .mockRejectedValueOnce(new Error(`server leaked ${firstSecret}`))
      .mockResolvedValueOnce(aliceFixture);
    render(<AdminUserManagementPage />);
    const { dialog } = await openCreateDialog();
    fillCreateDialog(dialog, {
      username: "reader",
      displayName: "Reader draft",
      password: firstSecret,
    });

    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The user could not be created.",
    );
    expect(within(dialog).getByLabelText("Username")).toHaveValue("reader");
    expect(within(dialog).getByLabelText("Display name")).toHaveValue("Reader draft");
    expect(within(dialog).getByLabelText("Password")).toHaveValue("");
    expect(screen.queryByText(firstSecret)).not.toBeInTheDocument();
    expect(screen.queryByText(/server leaked/i)).not.toBeInTheDocument();

    fireEvent.change(within(dialog).getByLabelText("Password"), {
      target: { value: secondSecret },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    fireEvent.submit(within(dialog).getByRole("form"));

    await waitFor(() => expect(createAdminUser).toHaveBeenCalledTimes(2));
    expect(createAdminUser).toHaveBeenLastCalledWith({
      username: "reader",
      display_name: "Reader draft",
      password: secondSecret,
    });
  });

  it("resets a password without retaining the pending secret and restores focus", async () => {
    const pending = deferred<AdminUser>();
    const secret = "another correct horse battery staple";
    const updated = {
      ...aliceFixture,
      display_name: "Alice from reset response",
      updated_at: "2026-07-13T01:00:00Z",
    };
    vi.mocked(resetAdminUserPassword).mockReturnValue(pending.promise);
    render(<AdminUserManagementPage />);
    const { dialog, trigger } = await openResetDialog();
    const input = within(dialog).getByLabelText("New password");
    await waitFor(() => expect(input).toHaveFocus());
    fireEvent.change(input, { target: { value: secret } });

    fireEvent.click(within(dialog).getByRole("button", { name: "Reset password" }));
    fireEvent.submit(within(dialog).getByRole("form"));

    expect(resetAdminUserPassword).toHaveBeenCalledTimes(1);
    expect(resetAdminUserPassword).toHaveBeenCalledWith(aliceFixture.id, secret);
    expect(input).toHaveValue("");
    expect(screen.queryByDisplayValue(secret)).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();

    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.mouseDown(dialog.closest(".dialog-backdrop")!);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("dialog", { name: "Reset password for alice" })).toBeInTheDocument();

    pending.resolve(updated);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(within(rowFor("alice")).getByText("Alice from reset response")).toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("keeps reset open with a stable error, clears the secret, and permits retry", async () => {
    const firstSecret = "failed-reset-secret";
    const secondSecret = "replacement-secret";
    const firstAttempt = deferred<AdminUser>();
    vi.mocked(resetAdminUserPassword)
      .mockReturnValueOnce(firstAttempt.promise)
      .mockResolvedValueOnce(aliceFixture);
    render(<AdminUserManagementPage />);
    const { dialog } = await openResetDialog();
    expect(within(dialog).getByLabelText("New password")).toHaveAttribute(
      "maxlength",
      "512",
    );
    fireEvent.change(within(dialog).getByLabelText("New password"), {
      target: { value: firstSecret },
    });

    fireEvent.click(within(dialog).getByRole("button", { name: "Reset password" }));

    await waitFor(() => expect(dialog).toHaveFocus());
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(dialog).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(dialog).toHaveFocus();
    firstAttempt.reject(new Error(`token details ${firstSecret}`));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The password could not be reset.",
    );
    expect(within(dialog).getByLabelText("New password")).toHaveValue("");
    expect(screen.queryByText(firstSecret)).not.toBeInTheDocument();
    expect(screen.queryByText(/token details/i)).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Reset password" })).toBeEnabled();
    await waitFor(() =>
      expect(within(dialog).getByLabelText("New password")).toHaveFocus(),
    );

    fireEvent.change(within(dialog).getByLabelText("New password"), {
      target: { value: secondSecret },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Reset password" }));
    fireEvent.submit(within(dialog).getByRole("form"));

    await waitFor(() => expect(resetAdminUserPassword).toHaveBeenCalledTimes(2));
    expect(resetAdminUserPassword).toHaveBeenLastCalledWith(
      aliceFixture.id,
      secondSecret,
    );
  });

  it("validates reset password bounds without calling the API", async () => {
    render(<AdminUserManagementPage />);
    const { dialog } = await openResetDialog();
    fireEvent.change(within(dialog).getByLabelText("New password"), {
      target: { value: "short" },
    });

    fireEvent.click(within(dialog).getByRole("button", { name: "Reset password" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "Password must contain 6 to 256 characters.",
    );
    expect(resetAdminUserPassword).not.toHaveBeenCalled();
    expect(within(dialog).getByLabelText("New password")).toHaveValue("");
  });

  it("cancels status confirmation without acquiring a mutation", async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    render(<AdminUserManagementPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Disable alice" }));

    expect(window.confirm).toHaveBeenCalledWith("Disable alice?");
    expect(setAdminUserStatus).not.toHaveBeenCalled();
    expect(within(rowFor("alice")).getByText("Active")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Disable alice" })).toBeEnabled();
  });

  it("enables an inactive ordinary user and applies the server response", async () => {
    const inactive = {
      ...aliceFixture,
      id: 4,
      username: "bob",
      display_name: "Bob",
      is_active: false,
    };
    vi.mocked(listAdminUsers).mockResolvedValue({ users: [inactive] });
    vi.mocked(setAdminUserStatus).mockResolvedValue({
      ...inactive,
      display_name: "Bob from server",
      is_active: true,
    });
    render(<AdminUserManagementPage />);

    fireEvent.click(await screen.findByRole("button", { name: "Enable bob" }));

    expect(window.confirm).toHaveBeenCalledWith("Enable bob?");
    expect(setAdminUserStatus).toHaveBeenCalledWith(inactive.id, true);
    expect(await screen.findByRole("button", { name: "Disable bob" })).toBeEnabled();
    expect(within(rowFor("bob")).getByText("Bob from server")).toBeInTheDocument();
    expect(within(rowFor("bob")).getByText("Active")).toBeInTheDocument();
  });

  it("does not update status optimistically and applies the complete server response", async () => {
    const pending = deferred<AdminUser>();
    const serverUser = {
      ...aliceFixture,
      username: "alice-server",
      display_name: "From server",
      is_active: false,
      updated_at: "2026-07-13T01:00:00Z",
    };
    vi.mocked(setAdminUserStatus).mockReturnValue(pending.promise);
    render(<AdminUserManagementPage />);
    const disable = await screen.findByRole("button", { name: "Disable alice" });

    fireEvent.click(disable);
    fireEvent.click(disable);

    expect(setAdminUserStatus).toHaveBeenCalledTimes(1);
    expect(setAdminUserStatus).toHaveBeenCalledWith(aliceFixture.id, false);
    expect(window.confirm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("rowheader", { name: "alice" })).toBeInTheDocument();
    expect(within(rowFor("alice")).getByText("Active")).toBeInTheDocument();
    expect(disable).toBeDisabled();

    pending.resolve(serverUser);
    expect(await screen.findByRole("rowheader", { name: "alice-server" })).toBeInTheDocument();
    expect(screen.getByText("From server")).toBeInTheDocument();
    expect(screen.getByText("Inactive")).toBeInTheDocument();
  });

  it("keeps the list visible after status failure, unlocks, and retries once", async () => {
    vi.mocked(setAdminUserStatus)
      .mockRejectedValueOnce(new Error("status database internals"))
      .mockResolvedValueOnce({ ...aliceFixture, is_active: false });
    render(<AdminUserManagementPage />);
    const disable = await screen.findByRole("button", { name: "Disable alice" });

    fireEvent.click(disable);
    fireEvent.click(disable);

    const pageAlert = await screen.findByRole("alert");
    expect(pageAlert).toHaveTextContent(
      "The user status could not be changed.",
    );
    expect(pageAlert.closest('[role="dialog"]')).toBeNull();
    expect(screen.queryByText(/status database internals/i)).not.toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "alice" })).toBeInTheDocument();
    expect(setAdminUserStatus).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(disable).toBeEnabled());

    fireEvent.click(disable);
    fireEvent.click(disable);
    await waitFor(() => expect(setAdminUserStatus).toHaveBeenCalledTimes(2));
    expect(window.confirm).toHaveBeenCalledTimes(2);
    expect(await screen.findByText("Inactive")).toBeInTheDocument();
    expect(
      screen.queryByText("The user status could not be changed."),
    ).not.toBeInTheDocument();
  });

  it("provides a labelled focus-trapped dialog with every close path", async () => {
    const view = render(<AdminUserManagementPage />);
    const backgroundStatus = await screen.findByRole("button", {
      name: "Disable alice",
    });
    const { dialog, trigger } = await openCreateDialog();
    const username = within(dialog).getByLabelText("Username");
    const displayName = within(dialog).getByLabelText("Display name");
    const password = within(dialog).getByLabelText("Password");
    const submit = within(dialog).getByRole("button", { name: "Create" });
    const form = within(dialog).getByRole("form");
    const background = view.container.querySelector(".management-content");
    await waitFor(() => expect(username).toHaveFocus());

    expect(dialog).toHaveAttribute("tabindex", "-1");
    expect(background).toHaveAttribute("aria-hidden", "true");
    expect(background).toHaveAttribute("inert");
    expect(form).toHaveAttribute("novalidate");
    expect(username).toBeRequired();
    expect(username).toHaveAttribute("minlength", "3");
    expect(username).toHaveAttribute("maxlength", "80");
    expect(username).toHaveAttribute("pattern", "[A-Za-z0-9_.-]+");
    expect(displayName).toHaveAttribute("maxlength", "240");
    expect(password).toBeRequired();
    expect(password).toHaveAttribute("minlength", "6");
    expect(password).toHaveAttribute("maxlength", "512");
    expect(password).toHaveAttribute("type", "password");
    expect(password).toHaveAttribute("autocomplete", "new-password");
    expect(backgroundStatus).toBeDisabled();
    backgroundStatus.removeAttribute("disabled");
    fireEvent.click(backgroundStatus);
    expect(window.confirm).not.toHaveBeenCalled();
    expect(setAdminUserStatus).not.toHaveBeenCalled();

    fireEvent.mouseDown(dialog);
    expect(screen.getByRole("dialog", { name: "Create user" })).toBeInTheDocument();

    submit.focus();
    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(username).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(submit).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(background).toHaveAttribute("aria-hidden", "false");
    expect(background).not.toHaveAttribute("inert");
    await waitFor(() => expect(trigger).toHaveFocus());

    fireEvent.click(trigger);
    const reopened = screen.getByRole("dialog", { name: "Create user" });
    fireEvent.mouseDown(reopened.closest(".dialog-backdrop")!);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(trigger);
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "cancel-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(trigger);
    expect(screen.getByLabelText("Password")).toHaveValue("");
  });

  it("keeps create focus and close paths trapped while pending", async () => {
    const pending = deferred<AdminUser>();
    vi.mocked(createAdminUser).mockReturnValue(pending.promise);
    render(<AdminUserManagementPage />);
    const { dialog } = await openCreateDialog();
    fillCreateDialog(dialog);
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    await waitFor(() => expect(createAdminUser).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(dialog).toHaveFocus());

    fireEvent.keyDown(dialog, { key: "Tab" });
    expect(dialog).toHaveFocus();
    fireEvent.keyDown(dialog, { key: "Tab", shiftKey: true });
    expect(dialog).toHaveFocus();

    fireEvent.keyDown(dialog, { key: "Escape" });
    fireEvent.mouseDown(dialog.closest(".dialog-backdrop")!);
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("dialog", { name: "Create user" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    pending.reject(new Error("create failed"));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "The user could not be created.",
    );
    await waitFor(() =>
      expect(within(dialog).getByLabelText("Username")).toHaveFocus(),
    );
  });

  it("ignores a stale StrictMode load completion", async () => {
    const stale = deferred<{ users: AdminUser[] }>();
    const current = deferred<{ users: AdminUser[] }>();
    const staleUser = { ...aliceFixture, id: 99, username: "stale-user" };
    vi.mocked(listAdminUsers)
      .mockReturnValueOnce(stale.promise)
      .mockReturnValueOnce(current.promise);
    render(
      <StrictMode>
        <AdminUserManagementPage />
      </StrictMode>,
    );
    await waitFor(() => expect(listAdminUsers).toHaveBeenCalledTimes(2));

    current.resolve({ users: [aliceFixture] });
    expect(await screen.findByRole("rowheader", { name: "alice" })).toBeInTheDocument();
    await act(async () => {
      stale.resolve({ users: [staleUser] });
      await stale.promise;
    });

    expect(screen.queryByText("stale-user")).not.toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "alice" })).toBeInTheDocument();
  });

  it("ignores dialog mutation completion after unmount", async () => {
    const pending = deferred<AdminUser>();
    vi.mocked(createAdminUser).mockReturnValue(pending.promise);
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    const view = render(<AdminUserManagementPage />);
    const { dialog } = await openCreateDialog();
    fillCreateDialog(dialog, { password: "unmount-secret" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    expect(within(dialog).getByLabelText("Password")).toHaveValue("");
    view.unmount();

    await act(async () => {
      pending.resolve(aliceFixture);
      await pending.promise;
    });

    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it("uses complete Chinese resources without registration language", async () => {
    await i18next.changeLanguage("zh-CN");
    render(<AdminUserManagementPage />);

    expect(await screen.findByRole("heading", { name: "用户管理" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建用户" })).toBeInTheDocument();
    expect(screen.queryByText(/Create user/i)).not.toBeInTheDocument();
    expect(namespaces).toContain("admin");
    const chinese = i18next.getResourceBundle("zh-CN", "admin");
    const english = i18next.getResourceBundle("en", "admin");
    expect(leafKeys(chinese)).toEqual(leafKeys(english));
    expect(JSON.stringify(chinese)).not.toContain("注册");
    expect(
      within(rowFor("alice")).getByText(
        new Intl.DateTimeFormat("zh-CN").format(
          new Date(aliceFixture.created_at),
        ),
      ),
    ).toBeInTheDocument();
  });
});

function leafKeys(value: unknown, prefix = ""): string[] {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return [prefix];
  }
  return Object.entries(value)
    .flatMap(([key, child]) => leafKeys(child, prefix ? `${prefix}.${key}` : key))
    .sort();
}
