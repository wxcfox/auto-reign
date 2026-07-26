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

export type ResourceSidebarOptions = {
  /**
   * Whether the sidebar splits items into Personal and Public tabs. Resources
   * whose public form is genuinely shared (agents, knowledge) want the split;
   * Workspaces do not, because a public Workspace is only a template and its
   * files always belong to the caller.
   */
  tabs: boolean;
  /** Resetting this discards tab, query, and selection state. */
  resetKey: string;
};

export function useResourceSidebarSelection(
  items: ResourceItem[],
  options: ResourceSidebarOptions,
): ResourceSidebarSelection {
  const { resetKey, tabs } = options;
  const [tab, setTab] = useState<ResourceTab>("personal");
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [renderedKey, setRenderedKey] = useState(resetKey);

  // The management pages swap resource surfaces by re-rendering, not remounting.
  if (renderedKey !== resetKey) {
    setRenderedKey(resetKey);
    setTab("personal");
    setQuery("");
    setCollapsed(false);
    setSelectedId(null);
  }

  const showTabs = tabs;
  const activeTab: ResourceTab = showTabs ? tab : "personal";

  const visibleItems = useMemo(() => {
    const tabItems = showTabs
      ? items.filter((item) =>
          activeTab === "personal" ? item.scope === "private" : item.scope === "global",
        )
      : items;
    const normalizedQuery = query.trim().toLowerCase();
    return normalizedQuery
      ? tabItems.filter((item) => item.name.toLowerCase().includes(normalizedQuery))
      : tabItems;
  }, [activeTab, items, query, showTabs]);

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
