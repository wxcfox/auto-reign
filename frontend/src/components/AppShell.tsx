"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  ChevronDown,
  Database,
  LayoutDashboard,
  MessageSquareText,
  MoreHorizontal,
  Moon,
  PencilLine,
  Plus,
  UserCircle,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { LanguageSwitcher } from "@/components/LanguageSwitcher";
import { useTranslation } from "@/hooks/useTranslation";
import { listInterviewSessions } from "@/lib/api";
import { INTERVIEW_SESSIONS_CHANGED_EVENT } from "@/lib/interview-events";
import type { InterviewSessionHistoryItem } from "@/lib/types";

type AppShellProps = {
  children: ReactNode;
};

export function AppShell({ children }: AppShellProps) {
  const currentPath = usePathname();
  const { t } = useTranslation("common");
  const [sessions, setSessions] = useState<InterviewSessionHistoryItem[]>([]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [darkMode, setDarkMode] = useState(false);
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
  }, [darkMode]);

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

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-brand">
          <span className="app-brand-mark" aria-hidden="true">
            AR
          </span>
          <span>{t("app.title")}</span>
        </div>
        <Link className="new-chat-link" href="/interview">
          <Plus size={18} aria-hidden="true" />
          <span>{t("actions.new_interview")}</span>
        </Link>
        <Link className="new-chat-link new-learning-link" href="/learn">
          <PencilLine size={18} aria-hidden="true" />
          <span>{t("actions.new_learning")}</span>
        </Link>
        <nav aria-label="Primary" className="app-nav">
          {primaryNavItems.map((item) => {
            const Icon = item.icon;
            const active =
              item.href === "/" ? currentPath === item.href : currentPath.startsWith(item.href);
            return (
              <Link href={item.href} key={item.href} data-active={active}>
                <Icon size={18} aria-hidden="true" />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>
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
                  <span>{title}</span>
                  <small>{t("states.completed")}</small>
                </button>
              );
            }
            return (
              <Link
                className="sidebar-history-item"
                href={`/interview?session=${item.session.id}`}
                key={item.session.id}
              >
                <MessageSquareText size={16} aria-hidden="true" />
                <span>{title}</span>
                <small>{t("states.working")}</small>
              </Link>
            );
          })}
        </section>
        <section className="sidebar-more" aria-label={t("nav.more")}>
          <button
            className="sidebar-more-button"
            type="button"
            aria-expanded={moreOpen}
            onClick={() => setMoreOpen((current) => !current)}
          >
            <MoreHorizontal size={18} aria-hidden="true" />
            <span>{t("nav.more")}</span>
            <ChevronDown className="sidebar-more-chevron" size={16} aria-hidden="true" />
          </button>
          <div className="sidebar-more-list" data-open={moreOpen}>
            {secondaryNavItems.map((item) => {
              const Icon = item.icon;
              const active =
                item.href === "/" ? currentPath === item.href : currentPath.startsWith(item.href);
              return (
                <Link href={item.href} key={item.href} data-active={active}>
                  <Icon size={18} aria-hidden="true" />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </div>
        </section>
        <div className="app-sidebar-footer">
          <button
            aria-expanded={settingsOpen}
            className="sidebar-user"
            onClick={() => setSettingsOpen((current) => !current)}
            type="button"
          >
            <UserCircle size={18} aria-hidden="true" />
            <span>{t("app.user")}</span>
            <ChevronDown className="sidebar-more-chevron" size={16} aria-hidden="true" />
          </button>
          {settingsOpen ? (
            <div className="sidebar-settings-menu">
              <LanguageSwitcher />
              <label className="sidebar-settings-row">
                <Moon size={17} aria-hidden="true" />
                <span>{t("app.dark_mode")}</span>
                <input
                  checked={darkMode}
                  onChange={(event) => setDarkMode(event.target.checked)}
                  type="checkbox"
                />
              </label>
            </div>
          ) : null}
        </div>
      </aside>
      <main className="app-main">{children}</main>
    </div>
  );
}
