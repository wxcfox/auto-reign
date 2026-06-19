import type { ReactNode } from "react";
import { BookOpen, ClipboardList, Database, LayoutDashboard } from "lucide-react";

const navItems = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/library", label: "Library", icon: Database },
  { href: "/interview", label: "Interview", icon: ClipboardList },
  { href: "/review", label: "Review", icon: BookOpen },
];

type AppShellProps = {
  children: ReactNode;
  currentPath?: string;
};

export function AppShell({ children, currentPath = "/" }: AppShellProps) {
  return (
    <div className="app-shell">
      <aside className="app-sidebar">
        <div className="app-brand">
          <ClipboardList size={20} aria-hidden="true" />
          <span>Auto Reign</span>
        </div>
        <nav aria-label="Primary" className="app-nav">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = currentPath === item.href;
            return (
              <a href={item.href} key={item.href} data-active={active}>
                <Icon size={18} aria-hidden="true" />
                <span>{item.label}</span>
              </a>
            );
          })}
        </nav>
      </aside>
      <main className="app-main">{children}</main>
    </div>
  );
}
