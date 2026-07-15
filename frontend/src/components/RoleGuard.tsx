"use client";

import { useEffect, useState, type ReactNode } from "react";

import { useTranslation } from "@/hooks/useTranslation";
import { getCurrentUser } from "@/lib/api";
import type { User } from "@/lib/types";

export interface RoleGuardProps {
  children: ReactNode;
  role: User["role"];
}

export function RoleGuard({ children, role }: RoleGuardProps) {
  const { t } = useTranslation("common");
  const [state, setState] = useState<"loading" | "allowed" | "forbidden" | "error">(
    "loading",
  );

  useEffect(() => {
    let active = true;
    setState("loading");
    getCurrentUser()
      .then((user) => {
        if (active) {
          setState(user.role === role ? "allowed" : "forbidden");
        }
      })
      .catch(() => {
        if (active) {
          setState("error");
        }
      });
    return () => {
      active = false;
    };
  }, [role]);

  if (state === "loading") {
    return <p role="status">{t("permissions.loading")}</p>;
  }
  if (state === "forbidden") {
    return <p role="alert">{t("permissions.admin_required")}</p>;
  }
  if (state === "error") {
    return <p role="alert">{t("permissions.load_failed")}</p>;
  }
  return <>{children}</>;
}
