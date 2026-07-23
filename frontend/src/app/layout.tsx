import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/AppShell";
import { AuthGuard } from "@/components/AuthGuard";
import { I18nProvider } from "@/components/I18nProvider";
import { SocketProvider } from "@/contexts/SocketContext";

import "./globals.css";

export const metadata: Metadata = {
  title: "Auto Reign",
  description: "Local-first Agent chat platform",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <I18nProvider>
          <AuthGuard>
            <SocketProvider>
              <AppShell>{children}</AppShell>
            </SocketProvider>
          </AuthGuard>
        </I18nProvider>
      </body>
    </html>
  );
}
