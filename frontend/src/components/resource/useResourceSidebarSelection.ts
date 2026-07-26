"use client";

import { useMemo, useState } from "react";

export type ResourceScope = "private" | "global";
export type ResourceTab = "personal" | "global";

export type ResourceItem = {
  id: string;
  name: string;
  scope: ResourceScope;
  isActive: boolean;
  manageable: boolean;
};

export type ResourceSidebarSelection = {
  tab: ResourceTab;
  setTab: (tab: ResourceTab) => void;
  query: string;
  setQuery: (query: string) => void;
  collapsed: boolean;
  setCollapsed: (collapsed: boolean) => void;
  showTabs: boolean;
  visibleItems: ResourceItem[];
  selectedItem: ResourceItem | null;
  selectItem: (id: string) => void;
};

export function useResourceSidebarSelection(
  items: ResourceItem[],
  scope: ResourceScope,
): ResourceSidebarSelection {
  const [tab, setTab] = useState<ResourceTab>("personal");
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [renderedScope, setRenderedScope] = useState(scope);

  // The management pages swap `scope` by re-rendering rather than remounting.
  if (renderedScope !== scope) {
    setRenderedScope(scope);
    setTab("personal");
    setQuery("");
    setCollapsed(false);
    setSelectedId(null);
  }

  const showTabs = scope === "private";
  const activeTab: ResourceTab = showTabs ? tab : "global";

  const visibleItems = useMemo(() => {
    const tabItems = items.filter((item) =>
      activeTab === "personal" ? item.scope === "private" : item.scope === "global",
    );
    const normalizedQuery = query.trim().toLowerCase();
    return normalizedQuery
      ? tabItems.filter((item) => item.name.toLowerCase().includes(normalizedQuery))
      : tabItems;
  }, [activeTab, items, query]);

  // Selection follows the filtered list so the detail pane never shows a row the
  // sidebar has filtered away.
  const selectedItem = useMemo(
    () =>
      visibleItems.find((item) => item.id === selectedId) ?? visibleItems[0] ?? null,
    [selectedId, visibleItems],
  );

  return {
    tab: activeTab,
    setTab,
    query,
    setQuery,
    collapsed,
    setCollapsed,
    showTabs,
    visibleItems,
    selectedItem,
    selectItem: setSelectedId,
  };
}
