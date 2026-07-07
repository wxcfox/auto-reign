"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from "react";
import {
  ChevronDown,
  ChevronUp,
  Database,
  LayoutDashboard,
  Languages,
  LogOut,
  MessageSquareText,
  MoreHorizontal,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  PencilLine,
  Plus,
  Sun,
  Trash2,
  UserCircle,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { useTranslation } from "@/hooks/useTranslation";
import { deleteConversation, getCurrentUser, listConversations, renameConversation } from "@/lib/api";
import { clearAuthToken } from "@/lib/auth";
import { CONVERSATIONS_CHANGED_EVENT } from "@/lib/conversation-events";
import type { ConversationHistoryItem } from "@/lib/types";

type AppShellProps = {
  children: ReactNode;
};

function isHistoryMenuSurfaceTarget(target: EventTarget | null) {
  return target instanceof Element && target.closest("[data-history-menu-surface]") !== null;
}

function stopHistoryMenuPointerDown(event: ReactPointerEvent<HTMLElement>) {
  event.stopPropagation();
  event.nativeEvent.stopImmediatePropagation();
}

function conversationLandingPath(kind: ConversationHistoryItem["kind"]) {
  return kind === "learning" ? "/learn" : "/interview";
}

function isCurrentBrowserConversation(item: ConversationHistoryItem) {
  if (typeof window === "undefined") {
    return false;
  }
  return `${window.location.pathname}${window.location.search}` === item.href;
}

function readPreferredDarkMode() {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    return window.localStorage?.getItem("preferred-theme") === "dark";
  } catch {
    return false;
  }
}

function writePreferredTheme(darkMode: boolean) {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage?.setItem("preferred-theme", darkMode ? "dark" : "light");
  } catch {
    // Theme changes should still work in restricted storage environments.
  }
}

function readSidebarCollapsed() {
  if (typeof window === "undefined") {
    return false;
  }
  try {
    return window.localStorage?.getItem("sidebar-collapsed") === "true";
  } catch {
    return false;
  }
}

function writeSidebarCollapsed(collapsed: boolean) {
  if (typeof window === "undefined") {
    return;
  }
  try {
    window.localStorage?.setItem("sidebar-collapsed", collapsed ? "true" : "false");
  } catch {
    // Sidebar state should not depend on persistent storage being available.
  }
}

export function AppShell({ children }: AppShellProps) {
  const currentPath = usePathname();
  const router = useRouter();
  const { changeLanguage, getCurrentLanguage, t } = useTranslation("common");
  const [conversations, setConversations] = useState<ConversationHistoryItem[]>([]);
  const [historyMenuKey, setHistoryMenuKey] = useState<string | null>(null);
  const [historyActionError, setHistoryActionError] = useState<string | null>(null);
  const [historyActionPendingKey, setHistoryActionPendingKey] = useState<string | null>(null);
  const [renamingConversation, setRenamingConversation] =
    useState<ConversationHistoryItem | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [currentUserId, setCurrentUserId] = useState<number | null>(null);
  const [darkMode, setDarkMode] = useState(readPreferredDarkMode);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readSidebarCollapsed);
  const conversationRefreshId = useRef(0);
  const mountedRef = useRef(false);
  const primaryNavItems = [
    { href: "/library", label: t("nav.library"), icon: Database },
  ];
  const secondaryNavItems = [
    { href: "/", label: t("nav.workbench"), icon: LayoutDashboard },
  ];
  const secondaryNavActive = secondaryNavItems.some((item) =>
    item.href === "/" ? currentPath === item.href : currentPath.startsWith(item.href),
  );
  const isAuthPage = currentPath === "/login" || currentPath === "/register";
  const [moreOpen, setMoreOpen] = useState(secondaryNavActive);

  const refreshConversations = useCallback(async () => {
    if (isAuthPage) {
      setConversations([]);
      return;
    }
    const refreshId = conversationRefreshId.current + 1;
    conversationRefreshId.current = refreshId;
    try {
      const response = await listConversations();
      if (mountedRef.current && refreshId === conversationRefreshId.current) {
        setConversations(response.conversations);
      }
    } catch {
      if (mountedRef.current && refreshId === conversationRefreshId.current) {
        setConversations([]);
      }
    }
  }, [isAuthPage]);

  useEffect(() => {
    mountedRef.current = true;

    if (isAuthPage) {
      setConversations([]);
      setCurrentUserId(null);
      return () => {
        mountedRef.current = false;
      };
    }

    void refreshConversations();
    const handleConversationsChanged = () => {
      void refreshConversations();
    };
    window.addEventListener(CONVERSATIONS_CHANGED_EVENT, handleConversationsChanged);

    return () => {
      mountedRef.current = false;
      window.removeEventListener(CONVERSATIONS_CHANGED_EVENT, handleConversationsChanged);
    };
  }, [isAuthPage, refreshConversations]);

  useEffect(() => {
    if (isAuthPage) {
      setCurrentUserId(null);
      return;
    }
    let cancelled = false;
    getCurrentUser()
      .then((user) => {
        if (!cancelled) {
          setCurrentUserId(user.id);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCurrentUserId(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [isAuthPage]);

  useEffect(() => {
    if (historyMenuKey === null) {
      return;
    }
    const closeMenu = (event: PointerEvent) => {
      if (isHistoryMenuSurfaceTarget(event.target)) {
        return;
      }
      setHistoryMenuKey(null);
    };
    document.addEventListener("pointerdown", closeMenu);
    return () => document.removeEventListener("pointerdown", closeMenu);
  }, [historyMenuKey]);

  useEffect(() => {
    document.documentElement.dataset.theme = darkMode ? "dark" : "light";
    writePreferredTheme(darkMode);
  }, [darkMode]);

  useEffect(() => {
    writeSidebarCollapsed(sidebarCollapsed);
  }, [sidebarCollapsed]);

  const currentLanguage = getCurrentLanguage();
  const nextLanguage = currentLanguage === "zh-CN" ? "en" : "zh-CN";
  const nextLanguageLabel = currentLanguage === "zh-CN" ? "English" : "简体中文";
  const ThemeIcon = darkMode ? Sun : Moon;
  const SidebarIcon = sidebarCollapsed ? PanelLeftOpen : PanelLeftClose;
  const UserMenuIcon = settingsOpen ? ChevronDown : ChevronUp;
  const userLabel =
    currentUserId === null ? t("app.user") : t("app.user_id", { id: currentUserId });

  function conversationKey(item: ConversationHistoryItem) {
    return `${item.kind}:${item.id}`;
  }

  function openRenameDialog(item: ConversationHistoryItem, title: string) {
    setHistoryActionError(null);
    setHistoryMenuKey(null);
    setRenamingConversation(item);
    setRenameValue(title);
  }

  async function handleRenameSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!renamingConversation) {
      return;
    }
    const title = renameValue.trim();
    if (!title) {
      return;
    }
    const pendingKey = conversationKey(renamingConversation);
    setHistoryActionPendingKey(pendingKey);
    setHistoryActionError(null);
    try {
      const renamedConversation = await renameConversation(renamingConversation.id, title);
      setConversations((current) =>
        current.map((conversation) =>
          conversationKey(conversation) === pendingKey ? renamedConversation : conversation,
        ),
      );
      setRenamingConversation(null);
      setRenameValue("");
    } catch {
      setHistoryActionError(t("errors.generic_save"));
    } finally {
      setHistoryActionPendingKey(null);
    }
  }

  async function handleDeleteConversation(item: ConversationHistoryItem, title: string) {
    setHistoryMenuKey(null);
    const confirmed = window.confirm(t("actions.delete_conversation_confirm", { title }));
    if (!confirmed) {
      return;
    }
    const pendingKey = conversationKey(item);
    const deletingCurrentConversation = isCurrentBrowserConversation(item);
    setHistoryActionPendingKey(pendingKey);
    setHistoryActionError(null);
    try {
      await deleteConversation(item.id);
      setConversations((current) =>
        current.filter((conversation) => conversationKey(conversation) !== pendingKey),
      );
      if (deletingCurrentConversation) {
        router.replace(conversationLandingPath(item.kind));
      }
      await refreshConversations();
    } catch {
      setHistoryActionError(t("errors.generic_save"));
    } finally {
      setHistoryActionPendingKey(null);
    }
  }

  function handleLogout() {
    clearAuthToken();
    setSettingsOpen(false);
    router.replace("/login");
  }

  if (isAuthPage) {
    return <>{children}</>;
  }

  return (
    <div className="app-shell" data-sidebar-collapsed={sidebarCollapsed}>
      <aside className="app-sidebar">
        <div className="app-brand">
          <span className="app-brand-mark" aria-hidden="true">
            AR
          </span>
          <span className="sidebar-label app-brand-title">{t("app.title")}</span>
          <button
            aria-label={sidebarCollapsed ? t("app.expand_sidebar") : t("app.collapse_sidebar")}
            className="sidebar-collapse-button"
            onClick={() => setSidebarCollapsed((current) => !current)}
            type="button"
          >
            <SidebarIcon size={18} aria-hidden="true" />
          </button>
        </div>
        <Link className="new-chat-link" href="/interview" aria-label={t("actions.new_interview")}>
          <Plus size={18} aria-hidden="true" />
          <span className="sidebar-label">{t("actions.new_interview")}</span>
        </Link>
        <Link className="new-chat-link new-learning-link" href="/learn" aria-label={t("actions.new_learning")}>
          <PencilLine size={18} aria-hidden="true" />
          <span className="sidebar-label">{t("actions.new_learning")}</span>
        </Link>
        <nav aria-label="Primary" className="app-nav">
          {primaryNavItems.map((item) => {
            const Icon = item.icon;
            const active =
              item.href === "/" ? currentPath === item.href : currentPath.startsWith(item.href);
            return (
              <Link href={item.href} key={item.href} data-active={active} aria-label={item.label}>
                <Icon size={18} aria-hidden="true" />
                <span className="sidebar-label">{item.label}</span>
              </Link>
            );
          })}
        </nav>
        <section className="sidebar-more" aria-label={t("nav.more")}>
          <button
            className="sidebar-more-button"
            type="button"
            aria-expanded={moreOpen}
            aria-label={t("nav.more")}
            onClick={() => setMoreOpen((current) => !current)}
          >
            <MoreHorizontal size={18} aria-hidden="true" />
            <span className="sidebar-label">{t("nav.more")}</span>
            <ChevronDown className="sidebar-more-chevron sidebar-label" size={16} aria-hidden="true" />
          </button>
          <div className="sidebar-more-list" data-open={moreOpen}>
            {secondaryNavItems.map((item) => {
              const Icon = item.icon;
              const active =
                item.href === "/" ? currentPath === item.href : currentPath.startsWith(item.href);
              return (
                <Link href={item.href} key={item.href} data-active={active} aria-label={item.label}>
                  <Icon size={18} aria-hidden="true" />
                  <span className="sidebar-label">{item.label}</span>
                </Link>
              );
            })}
          </div>
        </section>
        <section className="sidebar-history" aria-labelledby="sidebar-history-heading">
          <h2 id="sidebar-history-heading">{t("nav.history")}</h2>
          {historyActionError ? (
            <p className="sidebar-history-error" role="alert">
              {historyActionError}
            </p>
          ) : null}
          {conversations.length === 0 ? (
            <p className="sidebar-history-empty">{t("nav.empty_history")}</p>
          ) : null}
          {conversations.map((item) => {
            const title = item.title || t("nav.untitled_session");
            const itemKey = conversationKey(item);
            const menuOpen = historyMenuKey === itemKey;
            const pending = historyActionPendingKey === itemKey;
            return (
              <div className="sidebar-history-row" key={itemKey}>
                <Link className="sidebar-history-item" href={item.href} aria-label={title} title={title}>
                  <MessageSquareText size={16} aria-hidden="true" />
                  <span className="sidebar-label">{title}</span>
                </Link>
                <button
                  aria-expanded={menuOpen}
                  aria-label={t("actions.conversation_actions", { title })}
                  className="sidebar-history-action"
                  data-history-menu-surface="true"
                  disabled={pending}
                  onClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    setHistoryMenuKey((current) => (current === itemKey ? null : itemKey));
                  }}
                  onPointerDown={stopHistoryMenuPointerDown}
                  type="button"
                >
                  <MoreHorizontal size={15} aria-hidden="true" />
                </button>
                {menuOpen ? (
                  <div
                    className="sidebar-history-menu"
                    data-history-menu-surface="true"
                    role="menu"
                    onClick={(event) => event.stopPropagation()}
                    onPointerDown={stopHistoryMenuPointerDown}
                  >
                    <button
                      onClick={() => openRenameDialog(item, title)}
                      role="menuitem"
                      type="button"
                    >
                      <PencilLine size={15} aria-hidden="true" />
                      <span>{t("actions.rename_conversation")}</span>
                    </button>
                    <button
                      className="sidebar-history-menu-danger"
                      onClick={() => void handleDeleteConversation(item, title)}
                      role="menuitem"
                      type="button"
                    >
                      <Trash2 size={15} aria-hidden="true" />
                      <span>{t("actions.delete_conversation")}</span>
                    </button>
                  </div>
                ) : null}
              </div>
            );
          })}
        </section>
        <div className="app-sidebar-footer">
          <button
            aria-expanded={settingsOpen}
            aria-label={userLabel}
            className="sidebar-user"
            onClick={() => setSettingsOpen((current) => !current)}
            type="button"
          >
            <UserCircle size={18} aria-hidden="true" />
            <span className="sidebar-label">{userLabel}</span>
            <UserMenuIcon className="sidebar-user-chevron sidebar-label" size={16} aria-hidden="true" />
          </button>
          {settingsOpen ? (
            <div className="sidebar-settings-menu">
              <button
                aria-label={t("app.switch_language_to", { language: nextLanguageLabel })}
                className="sidebar-settings-action"
                onClick={() => changeLanguage(nextLanguage)}
                type="button"
              >
                <Languages size={17} aria-hidden="true" />
                <span>{nextLanguageLabel}</span>
              </button>
              <button
                aria-label={darkMode ? t("app.light_mode") : t("app.dark_mode")}
                className="sidebar-settings-action"
                onClick={() => setDarkMode((current) => !current)}
                type="button"
              >
                <ThemeIcon size={17} aria-hidden="true" />
                <span>{darkMode ? t("app.light_mode") : t("app.dark_mode")}</span>
              </button>
              <button
                aria-label={t("app.logout")}
                className="sidebar-settings-action"
                onClick={handleLogout}
                type="button"
              >
                <LogOut size={17} aria-hidden="true" />
                <span>{t("app.logout")}</span>
              </button>
            </div>
          ) : null}
        </div>
      </aside>
      <main className="app-main">{children}</main>
      {renamingConversation ? (
        <div className="dialog-backdrop">
          <form
            aria-labelledby="rename-conversation-title"
            aria-modal="true"
            className="dialog-panel rename-conversation-dialog"
            onSubmit={handleRenameSubmit}
            role="dialog"
          >
            <div className="dialog-heading">
              <h2 id="rename-conversation-title">{t("actions.rename_conversation")}</h2>
              <p>{t("actions.rename_conversation_description")}</p>
            </div>
            <label htmlFor="rename-conversation-input">
              {t("actions.conversation_name")}
            </label>
            <input
              autoFocus
              id="rename-conversation-input"
              maxLength={120}
              onChange={(event) => setRenameValue(event.target.value)}
              value={renameValue}
            />
            {historyActionError ? (
              <p className="form-error" role="alert">
                {historyActionError}
              </p>
            ) : null}
            <div className="dialog-actions">
              <button
                className="button"
                onClick={() => {
                  setHistoryActionError(null);
                  setRenamingConversation(null);
                  setRenameValue("");
                }}
                type="button"
              >
                {t("actions.cancel")}
              </button>
              <button
                className="button button-primary"
                disabled={!renameValue.trim() || historyActionPendingKey !== null}
                type="submit"
              >
                {t("actions.save")}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </div>
  );
}
