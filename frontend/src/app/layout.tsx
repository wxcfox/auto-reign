import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/AppShell";
import { AuthGuard } from "@/components/AuthGuard";
import { I18nProvider } from "@/components/I18nProvider";

import "./globals.css";

export const metadata: Metadata = {
  title: "Auto Reign",
  description: "Local mock interview workspace",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <I18nProvider>
          <AuthGuard>
            <AppShell>{children}</AppShell>
          </AuthGuard>
        </I18nProvider>
      </body>
    </html>
  );
}
