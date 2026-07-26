"use client";

import type { ReactNode } from "react";

import type { ResourceScope } from "./useResourceSidebarSelection";

export type ResourceSidebarLayoutProps = {
  children: ReactNode;
  /** Present only on surfaces that still split Personal and Public. */
  scope?: ResourceScope;
  sidebar: ReactNode;
  titleId: string;
};

export function ResourceSidebarLayout({
  children,
  scope,
  sidebar,
  titleId,
}: ResourceSidebarLayoutProps) {
  return (
    <section aria-labelledby={titleId} className="resource-shell" data-scope={scope}>
      {sidebar}
      <div className="resource-main">{children}</div>
    </section>
  );
}
