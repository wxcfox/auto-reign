"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ChevronDown,
  ChevronUp,
  Database,
  LayoutDashboard,
  Languages,
  MessageSquareText,
  MoreHorizontal,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  PencilLine,
  Plus,
  Sun,
  UserCircle,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useTranslation } from "@/hooks/useTranslation";
import { listInterviewSessions } from "@/lib/api";
import { INTERVIEW_SESSIONS_CHANGED_EVENT } from "@/lib/interview-events";
import type { InterviewSessionHistoryItem } from "@/lib/types";

type AppShellProps = {
  children: ReactNode;
};

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
  const { changeLanguage, getCurrentLanguage, t } = useTranslation("common");
  const [sessions, setSessions] = useState<InterviewSessionHistoryItem[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [darkMode, setDarkMode] = useState(readPreferredDarkMode);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(readSidebarCollapsed);
  const sessionRefreshId = useRef(0);
  const primaryNavItems = [
    { href: "/library", label: t("nav.library"), icon: Database },
  ];
  const secondaryNavItems = [
    { href: "/", label: t("nav.workbench"), icon: LayoutDashboard },
  ];
  const secondaryNavActive = secondaryNavItems.some((item) =>
    item.href === "/" ? currentPath === item.href : currentPath.startsWith(item.href),
  );
  const [moreOpen, setMoreOpen] = useState(secondaryNavActive);

  useEffect(() => {
    let cancelled = false;
    async function refreshSessions() {
      const refreshId = sessionRefreshId.current + 1;
      sessionRefreshId.current = refreshId;
      try {
        const response = await listInterviewSessions();
        if (!cancelled && refreshId === sessionRefreshId.current) {
          setSessions(response.sessions);
        }
      } catch {
        if (!cancelled && refreshId === sessionRefreshId.current) {
          setSessions([]);
        }
      }
    }

    void refreshSessions();
    const handleSessionsChanged = () => {
      void refreshSessions();
    };
    window.addEventListener(INTERVIEW_SESSIONS_CHANGED_EVENT, handleSessionsChanged);

    return () => {
      cancelled = true;
      window.removeEventListener(INTERVIEW_SESSIONS_CHANGED_EVENT, handleSessionsChanged);
    };
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = darkMode ? "dark" : "light";
    writePreferredTheme(darkMode);
  }, [darkMode]);

  useEffect(() => {
    writeSidebarCollapsed(sidebarCollapsed);
  }, [sidebarCollapsed]);

  function sessionTitle(item: InterviewSessionHistoryItem) {
    const naturalContext = item.config.extra_prompt.trim();
    if (naturalContext) {
      return naturalContext;
    }
    const structured = [item.config.target_company, item.config.target_role]
      .map((value) => value.trim())
      .filter(Boolean)
      .join(" ");
    return structured || t("nav.untitled_session");
  }

  const currentLanguage = getCurrentLanguage();
  const nextLanguage = currentLanguage === "zh-CN" ? "en" : "zh-CN";
  const nextLanguageLabel = currentLanguage === "zh-CN" ? "English" : "简体中文";
  const ThemeIcon = darkMode ? Sun : Moon;
  const SidebarIcon = sidebarCollapsed ? PanelLeftOpen : PanelLeftClose;
  const UserMenuIcon = settingsOpen ? ChevronDown : ChevronUp;

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
          {sessions.length === 0 ? (
            <p className="sidebar-history-empty">{t("nav.empty_history")}</p>
          ) : null}
          {sessions.map((item) => {
            const title = sessionTitle(item);
            if (!item.resumable) {
              return (
                <button className="sidebar-history-item" disabled key={item.session.id} type="button">
                  <MessageSquareText size={16} aria-hidden="true" />
                  <span className="sidebar-label">{title}</span>
                  <small className="sidebar-label">{t("states.completed")}</small>
                </button>
              );
            }
            return (
              <Link
                className="sidebar-history-item"
                href={`/interview?session=${item.session.id}`}
                aria-label={title}
                key={item.session.id}
              >
                <MessageSquareText size={16} aria-hidden="true" />
                <span className="sidebar-label">{title}</span>
                <small className="sidebar-label">{t("states.working")}</small>
              </Link>
            );
          })}
        </section>
        <div className="app-sidebar-footer">
          <button
            aria-expanded={settingsOpen}
            aria-label={t("app.user")}
            className="sidebar-user"
            onClick={() => setSettingsOpen((current) => !current)}
            type="button"
          >
            <UserCircle size={18} aria-hidden="true" />
            <span className="sidebar-label">{t("app.user")}</span>
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
            </div>
          ) : null}
        </div>
      </aside>
      <main className="app-main">{children}</main>
    </div>
  );
}
