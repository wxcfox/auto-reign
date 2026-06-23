"use client";

import { useState, type ReactNode } from "react";
import {
  BookOpen,
  ChevronDown,
  ClipboardList,
  Database,
  LayoutDashboard,
  MoreHorizontal,
  PencilLine,
  Plus,
  Settings,
  UserCircle,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useTranslation } from "@/hooks/useTranslation";

type AppShellProps = {
  children: ReactNode;
};

export function AppShell({ children }: AppShellProps) {
  const currentPath = usePathname();
  const { t } = useTranslation("common");
  const primaryNavItems = [
    { href: "/interview", label: t("nav.interview"), icon: ClipboardList },
    { href: "/library", label: t("nav.library"), icon: Database },
  ];
  const secondaryNavItems = [
    { href: "/", label: t("nav.dashboard"), icon: LayoutDashboard },
    { href: "/review", label: t("nav.review"), icon: BookOpen },
  ];
  const secondaryNavActive = secondaryNavItems.some((item) =>
    item.href === "/" ? currentPath === item.href : currentPath.startsWith(item.href),
  );
  const [moreOpen, setMoreOpen] = useState(secondaryNavActive);

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
          <h2 id="sidebar-history-heading">{t("nav.recent_sessions")}</h2>
          <Link href="/interview">{t("nav.current_session")}</Link>
          <Link href="/review">{t("nav.latest_review")}</Link>
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
          <button className="sidebar-user" type="button">
            <UserCircle size={18} aria-hidden="true" />
            <span>{t("app.user")}</span>
          </button>
          <button className="sidebar-user" type="button">
            <Settings size={18} aria-hidden="true" />
            <span>{t("app.settings")}</span>
          </button>
        </div>
      </aside>
      <main className="app-main">{children}</main>
    </div>
  );
}
