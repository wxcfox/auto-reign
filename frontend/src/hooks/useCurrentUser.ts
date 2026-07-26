"use client";

import { useEffect, useState } from "react";

import { getCurrentUser } from "@/lib/api";
import type { User } from "@/lib/types";

/** Load the authenticated user once, resolving to null when unauthenticated. */
export function useCurrentUser(): { user: User | null; loaded: boolean } {
  const [user, setUser] = useState<User | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getCurrentUser()
      .then((current) => {
        if (!cancelled) {
          setUser(current);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setUser(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoaded(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { user, loaded };
}
