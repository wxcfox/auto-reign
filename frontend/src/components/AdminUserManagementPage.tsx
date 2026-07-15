"use client";

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type MouseEvent,
} from "react";

import { useTranslation } from "@/hooks/useTranslation";
import {
  createAdminUser,
  listAdminUsers,
  resetAdminUserPassword,
  setAdminUserStatus,
} from "@/lib/api";
import {
  MAX_DISPLAY_NAME_LENGTH,
  MAX_PASSWORD_LENGTH,
  MAX_USERNAME_LENGTH,
  MIN_PASSWORD_LENGTH,
  MIN_USERNAME_LENGTH,
} from "@/lib/limits";
import type { AdminUser } from "@/lib/types";

type DialogState =
  | { kind: "create" }
  | { kind: "reset"; user: AdminUser }
  | null;

type ValidationErrorKey =
  | "errors.username_length"
  | "errors.username_format"
  | "errors.display_name_length"
  | "errors.password_length";

const USERNAME_PATTERN = /^[A-Za-z0-9_.-]+$/;
// Native maxLength counts UTF-16 code units, so astral code points need two units.
const DISPLAY_NAME_NATIVE_MAX_LENGTH = MAX_DISPLAY_NAME_LENGTH * 2;
const PASSWORD_NATIVE_MAX_LENGTH = MAX_PASSWORD_LENGTH * 2;
const FOCUSABLE_SELECTOR =
  "button, input:not([type='hidden']), select, textarea, [tabindex]:not([tabindex='-1'])";

function codePointLength(value: string) {
  return [...value].length;
}

function validateCreate(
  username: string,
  displayName: string,
  password: string,
): ValidationErrorKey | null {
  const usernameLength = codePointLength(username);
  if (
    usernameLength < MIN_USERNAME_LENGTH ||
    usernameLength > MAX_USERNAME_LENGTH
  ) {
    return "errors.username_length";
  }
  if (!USERNAME_PATTERN.test(username)) {
    return "errors.username_format";
  }
  if (codePointLength(displayName) > MAX_DISPLAY_NAME_LENGTH) {
    return "errors.display_name_length";
  }
  return validatePassword(password);
}

function validatePassword(password: string): ValidationErrorKey | null {
  const length = codePointLength(password);
  return length < MIN_PASSWORD_LENGTH || length > MAX_PASSWORD_LENGTH
    ? "errors.password_length"
    : null;
}

function focusableElements(container: HTMLElement | null) {
  return Array.from(
    container?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? [],
  ).filter((element) => !element.matches(":disabled"));
}

function replaceUser(users: AdminUser[], updated: AdminUser) {
  const index = users.findIndex((user) => user.id === updated.id);
  if (index < 0) {
    return [...users, updated];
  }
  return users.map((user) => (user.id === updated.id ? updated : user));
}

export function AdminUserManagementPage() {
  const { getCurrentLanguage, t } = useTranslation("admin");
  const dialogTitleId = useId();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [pageError, setPageError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogState>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [pendingUserId, setPendingUserId] = useState<number | null>(null);
  const [dialogPending, setDialogPending] = useState(false);
  const mountedRef = useRef(false);
  const lifecycleGenerationRef = useRef(0);
  const loadGenerationRef = useRef(0);
  const mutationSequenceRef = useRef(0);
  const activeMutationRef = useRef<number | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  const load = useCallback(async () => {
    const generation = ++loadGenerationRef.current;
    if (mountedRef.current) {
      setLoadState("loading");
    }
    try {
      const response = await listAdminUsers();
      if (!mountedRef.current || loadGenerationRef.current !== generation) {
        return;
      }
      setUsers(response.users);
      setLoadState("ready");
    } catch {
      if (mountedRef.current && loadGenerationRef.current === generation) {
        setLoadState("error");
      }
    }
  }, []);

  useEffect(() => {
    const lifecycleGeneration = ++lifecycleGenerationRef.current;
    mountedRef.current = true;
    activeMutationRef.current = null;
    void load();
    return () => {
      if (lifecycleGenerationRef.current === lifecycleGeneration) {
        mountedRef.current = false;
        lifecycleGenerationRef.current += 1;
        activeMutationRef.current = null;
      }
      loadGenerationRef.current += 1;
    };
  }, [load]);

  useEffect(() => {
    if (dialog !== null) {
      if (dialogPending) {
        dialogRef.current?.focus();
        return;
      }
      const [first] = focusableElements(dialogRef.current);
      first?.focus();
      return;
    }
    const trigger = returnFocusRef.current;
    if (trigger?.isConnected) {
      trigger.focus();
    }
  }, [dialog, dialogPending]);

  function startMutation() {
    if (activeMutationRef.current !== null) {
      return null;
    }
    const mutation = ++mutationSequenceRef.current;
    activeMutationRef.current = mutation;
    return mutation;
  }

  function isCurrentMutation(mutation: number, lifecycleGeneration: number) {
    return (
      mountedRef.current &&
      lifecycleGenerationRef.current === lifecycleGeneration &&
      activeMutationRef.current === mutation
    );
  }

  function finishMutation(mutation: number) {
    if (activeMutationRef.current === mutation) {
      activeMutationRef.current = null;
    }
  }

  function clearDialogState() {
    setDialog(null);
    setDialogError(null);
    setUsername("");
    setDisplayName("");
    setPassword("");
  }

  function closeDialog() {
    if (dialogPending || activeMutationRef.current !== null) {
      return;
    }
    clearDialogState();
  }

  function openDialog(next: Exclude<DialogState, null>, trigger: HTMLElement) {
    if (dialog !== null || activeMutationRef.current !== null) {
      return;
    }
    returnFocusRef.current = trigger;
    setDialogError(null);
    setUsername("");
    setDisplayName("");
    setPassword("");
    setDialog(next);
  }

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (dialog?.kind !== "create") {
      return;
    }
    const mutation = startMutation();
    if (mutation === null) {
      return;
    }
    const lifecycleGeneration = lifecycleGenerationRef.current;
    const normalizedUsername = username.trim();
    const normalizedDisplayName = displayName.trim();
    const transportPassword = password;
    setPassword("");
    const validationError = validateCreate(
      normalizedUsername,
      normalizedDisplayName,
      transportPassword,
    );
    if (validationError !== null) {
      setDialogError(t(validationError));
      finishMutation(mutation);
      return;
    }

    setDialogPending(true);
    setDialogError(null);
    let succeeded = false;
    try {
      const created = await createAdminUser({
        username: normalizedUsername,
        display_name: normalizedDisplayName,
        password: transportPassword,
      });
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        setUsers((current) => replaceUser(current, created));
        succeeded = true;
      }
    } catch {
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        setDialogError(t("errors.create_failed"));
      }
    } finally {
      const shouldUpdate = isCurrentMutation(mutation, lifecycleGeneration);
      finishMutation(mutation);
      if (shouldUpdate) {
        setPassword("");
        setDialogPending(false);
        if (succeeded) {
          clearDialogState();
        }
      }
    }
  }

  async function submitReset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (dialog?.kind !== "reset" || dialog.user.role !== "user") {
      return;
    }
    const mutation = startMutation();
    if (mutation === null) {
      return;
    }
    const lifecycleGeneration = lifecycleGenerationRef.current;
    const userId = dialog.user.id;
    const transportPassword = password;
    setPassword("");
    const validationError = validatePassword(transportPassword);
    if (validationError !== null) {
      setDialogError(t(validationError));
      finishMutation(mutation);
      return;
    }

    setDialogPending(true);
    setDialogError(null);
    let succeeded = false;
    try {
      const updated = await resetAdminUserPassword(userId, transportPassword);
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        setUsers((current) => replaceUser(current, updated));
        succeeded = true;
      }
    } catch {
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        setDialogError(t("errors.reset_failed"));
      }
    } finally {
      const shouldUpdate = isCurrentMutation(mutation, lifecycleGeneration);
      finishMutation(mutation);
      if (shouldUpdate) {
        setPassword("");
        setDialogPending(false);
        if (succeeded) {
          clearDialogState();
        }
      }
    }
  }

  async function toggleStatus(user: AdminUser) {
    if (
      user.role !== "user" ||
      dialog !== null ||
      activeMutationRef.current !== null
    ) {
      return;
    }
    const nextActive = !user.is_active;
    if (
      !window.confirm(
        t(nextActive ? "actions.enable_confirm" : "actions.disable_confirm", {
          name: user.username,
        }),
      )
    ) {
      return;
    }
    const mutation = startMutation();
    if (mutation === null) {
      return;
    }
    const lifecycleGeneration = lifecycleGenerationRef.current;
    setPendingUserId(user.id);
    setPageError(null);
    try {
      const updated = await setAdminUserStatus(user.id, nextActive);
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        setUsers((current) => replaceUser(current, updated));
      }
    } catch {
      if (isCurrentMutation(mutation, lifecycleGeneration)) {
        setPageError(t("errors.status_failed"));
      }
    } finally {
      const shouldUpdate = isCurrentMutation(mutation, lifecycleGeneration);
      finishMutation(mutation);
      if (shouldUpdate) {
        setPendingUserId(null);
      }
    }
  }

  function handleDialogKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      closeDialog();
      return;
    }
    if (event.key !== "Tab") {
      return;
    }
    const focusable = focusableElements(dialogRef.current);
    if (focusable.length === 0) {
      event.preventDefault();
      dialogRef.current?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function handleBackdrop(event: MouseEvent<HTMLDivElement>) {
    if (event.target === event.currentTarget) {
      closeDialog();
    }
  }

  if (loadState === "loading") {
    return (
      <section className="management-page">
        <p role="status">{t("states.loading")}</p>
      </section>
    );
  }
  if (loadState === "error") {
    return (
      <section className="management-page management-load-error" role="alert">
        <p>{t("states.load_failed")}</p>
        <button className="button" onClick={() => void load()} type="button">
          {t("actions.retry")}
        </button>
      </section>
    );
  }

  const controlsDisabled =
    dialog !== null || pendingUserId !== null || dialogPending;
  const dateFormatter = new Intl.DateTimeFormat(getCurrentLanguage());
  const dialogTitle =
    dialog?.kind === "create"
      ? t("create.title")
      : dialog?.kind === "reset"
        ? t("reset.title", { name: dialog.user.username })
        : "";

  return (
    <section className="management-page" aria-labelledby="admin-users-title">
      <div
        aria-hidden={dialog !== null}
        className="management-content"
        inert={dialog !== null ? true : undefined}
      >
        <header className="management-header">
          <div>
            <h1 id="admin-users-title">{t("title")}</h1>
            <p>{t("summary")}</p>
          </div>
          <button
            className="button button-primary"
            disabled={controlsDisabled}
            onClick={(event) => openDialog({ kind: "create" }, event.currentTarget)}
            type="button"
          >
            {t("actions.create_user")}
          </button>
        </header>

        {pageError ? (
          <p className="form-error" role="alert">
            {pageError}
          </p>
        ) : null}

        <div className="admin-user-table-wrap">
          <table className="admin-user-table">
            <caption>{t("table.caption")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("fields.username")}</th>
                <th scope="col">{t("fields.display_name")}</th>
                <th scope="col">{t("fields.status")}</th>
                <th scope="col">{t("fields.created_at")}</th>
                <th scope="col">{t("fields.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {users.length === 0 ? (
                <tr>
                  <td colSpan={5}>{t("states.empty")}</td>
                </tr>
              ) : (
                users.map((user) => {
                  const fixedAdmin = user.role !== "user";
                  const fixedReasonId = `fixed-admin-${user.id}`;
                  const rowPending = pendingUserId === user.id;
                  return (
                    <tr key={user.id}>
                      <th scope="row">{user.username}</th>
                      <td>{user.display_name}</td>
                      <td>
                        {user.is_active ? t("states.active") : t("states.inactive")}
                      </td>
                      <td>
                        <time dateTime={user.created_at}>
                          {dateFormatter.format(new Date(user.created_at))}
                        </time>
                      </td>
                      <td>
                        <div className="admin-user-actions">
                          <button
                            aria-describedby={fixedAdmin ? fixedReasonId : undefined}
                            aria-label={t(
                              user.is_active
                                ? "actions.disable_label"
                                : "actions.enable_label",
                              { name: user.username },
                            )}
                            className="button"
                            disabled={fixedAdmin || controlsDisabled}
                            onClick={() => void toggleStatus(user)}
                            type="button"
                          >
                            {rowPending
                              ? t("actions.working")
                              : user.is_active
                                ? t("actions.disable")
                                : t("actions.enable")}
                          </button>
                          <button
                            aria-describedby={fixedAdmin ? fixedReasonId : undefined}
                            aria-label={t("actions.reset_label", {
                              name: user.username,
                            })}
                            className="button"
                            disabled={fixedAdmin || controlsDisabled}
                            onClick={(event) => {
                              if (user.role === "user") {
                                openDialog(
                                  { kind: "reset", user },
                                  event.currentTarget,
                                );
                              }
                            }}
                            type="button"
                          >
                            {t("actions.reset_password")}
                          </button>
                        </div>
                        {fixedAdmin ? (
                          <p className="admin-user-fixed-note" id={fixedReasonId}>
                            {t("states.fixed_admin")}
                          </p>
                        ) : null}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {dialog !== null ? (
        <div className="dialog-backdrop" onMouseDown={handleBackdrop}>
          <div
            aria-labelledby={dialogTitleId}
            aria-modal="true"
            className="dialog-panel admin-user-dialog"
            onKeyDown={handleDialogKeyDown}
            ref={dialogRef}
            role="dialog"
            tabIndex={-1}
          >
            <div className="dialog-heading">
              <h2 id={dialogTitleId}>{dialogTitle}</h2>
            </div>
            <form
              aria-labelledby={dialogTitleId}
              className="admin-user-dialog-form"
              noValidate
              onSubmit={dialog.kind === "create" ? submitCreate : submitReset}
            >
              {dialog.kind === "create" ? (
                <>
                  <label>
                    {t("fields.username")}
                    <input
                      autoComplete="off"
                      disabled={dialogPending}
                      maxLength={MAX_USERNAME_LENGTH}
                      minLength={MIN_USERNAME_LENGTH}
                      onChange={(event) => setUsername(event.target.value)}
                      pattern="[A-Za-z0-9_.-]+"
                      required
                      value={username}
                    />
                  </label>
                  <label>
                    {t("fields.display_name")}
                    <input
                      autoComplete="name"
                      disabled={dialogPending}
                      maxLength={DISPLAY_NAME_NATIVE_MAX_LENGTH}
                      onChange={(event) => setDisplayName(event.target.value)}
                      value={displayName}
                    />
                  </label>
                  <label>
                    {t("fields.password")}
                    <input
                      autoComplete="new-password"
                      disabled={dialogPending}
                      maxLength={PASSWORD_NATIVE_MAX_LENGTH}
                      minLength={MIN_PASSWORD_LENGTH}
                      onChange={(event) => setPassword(event.target.value)}
                      required
                      type="password"
                      value={password}
                    />
                  </label>
                </>
              ) : (
                <label>
                  {t("fields.new_password")}
                  <input
                    autoComplete="new-password"
                    disabled={dialogPending}
                    maxLength={PASSWORD_NATIVE_MAX_LENGTH}
                    minLength={MIN_PASSWORD_LENGTH}
                    onChange={(event) => setPassword(event.target.value)}
                    required
                    type="password"
                    value={password}
                  />
                </label>
              )}

              {dialogError ? (
                <p className="form-error" role="alert">
                  {dialogError}
                </p>
              ) : null}

              <div className="dialog-actions">
                <button
                  className="button"
                  disabled={dialogPending}
                  onClick={closeDialog}
                  type="button"
                >
                  {t("actions.cancel")}
                </button>
                <button
                  className="button button-primary"
                  disabled={dialogPending}
                  type="submit"
                >
                  {dialog.kind === "create"
                    ? dialogPending
                      ? t("actions.creating")
                      : t("actions.create")
                    : dialogPending
                      ? t("actions.resetting")
                      : t("actions.reset_password")}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </section>
  );
}
