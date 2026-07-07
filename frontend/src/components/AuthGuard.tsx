"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { isAuthenticated } from "@/lib/auth";

const PUBLIC_PATHS = new Set(["/login", "/register"]);

export function AuthGuard({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const queryString = searchParams.toString();

  useEffect(() => {
    if (PUBLIC_PATHS.has(pathname)) {
      setReady(true);
      return;
    }
    if (!isAuthenticated()) {
      setReady(false);
      const currentUrl = queryString ? `${pathname}?${queryString}` : pathname;
      router.replace(`/login?redirect=${encodeURIComponent(currentUrl)}`);
      return;
    }
    setReady(true);
  }, [pathname, queryString, router]);

  if (!ready) {
    return null;
  }
  return <>{children}</>;
}
