"use client";

import type { ReactNode } from "react";
import { BookOpen, ClipboardList, Database, LayoutDashboard } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { useTranslation } from "@/hooks/useTranslation";
import { LanguageSwitcher } from "@/components/LanguageSwitcher";

type AppShellProps = {
  children: ReactNode;
};

export function AppShell({ children }: AppShellProps) {
  const currentPath = usePathname();
  const { t } = useTranslation("common");
  const navItems = [
    { href: "/", label: t("nav.dashboard"), icon: LayoutDashboard },
    { href: "/library", label: t("nav.library"), icon: Database },
    { href: "/interview", label: t("nav.interview"), icon: ClipboardList },
    { href: "/review", label: t("nav.review"), icon: BookOpen },
  ];

  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-brand">
          <ClipboardList size={20} aria-hidden="true" />
          <span>{t("app.title")}</span>
        </div>
        <nav aria-label="Primary" className="app-nav">
          {navItems.map((item) => {
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
        <div className="app-sidebar-footer">
          <LanguageSwitcher />
        </div>
      </aside>
      <main className="app-main">{children}</main>
    </div>
  );
}
