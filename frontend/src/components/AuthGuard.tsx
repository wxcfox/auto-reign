"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { isAuthenticated } from "@/lib/auth";

const PUBLIC_PATHS = new Set(["/login", "/setup"]);

export function AuthGuard({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const { replace } = useRouter();
  const [authorizedLocationKey, setAuthorizedLocationKey] = useState<string | null>(null);
  const queryString = searchParams.toString();
  const locationKey = queryString ? `${pathname}?${queryString}` : pathname;
  const isPublicPath = PUBLIC_PATHS.has(pathname);
  const hasCurrentToken = isPublicPath ? false : isAuthenticated();

  useEffect(() => {
    if (isPublicPath) {
      setAuthorizedLocationKey(null);
      return;
    }
    if (!isAuthenticated()) {
      setAuthorizedLocationKey(null);
      replace(`/login?redirect=${encodeURIComponent(locationKey)}`);
      return;
    }
    setAuthorizedLocationKey(locationKey);
  }, [isPublicPath, locationKey, replace]);

  if (isPublicPath) {
    return <>{children}</>;
  }
  if (!hasCurrentToken || authorizedLocationKey !== locationKey) {
    return null;
  }
  return <>{children}</>;
}
