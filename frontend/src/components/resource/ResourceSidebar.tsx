"use client";

import {
  Building2,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  User,
} from "lucide-react";

import type { ResourceItem, ResourceSidebarSelection } from "./useResourceSidebarSelection";

export type ResourceSidebarLabels = {
  activeBadge: string;
  collapse: string;
  create: string;
  empty: string;
  expand: string;
  globalTab: string;
  inactiveBadge: string;
  noResults: string;
  openItem: (name: string) => string;
  personalTab: string;
  searchLabel: string;
  searchPlaceholder: string;
};

export type ResourceSidebarProps = {
  createDisabled: boolean;
  labels: ResourceSidebarLabels;
  onCreate: () => void;
  selection: ResourceSidebarSelection;
  titleId: string;
  title: string;
};

export function ResourceSidebar({
  createDisabled,
  labels,
  onCreate,
  selection,
  titleId,
  title,
}: ResourceSidebarProps) {
  const { collapsed, query, selectedItem, showTabs, tab, visibleItems } = selection;

  return (
    <aside className="resource-sidebar" data-collapsed={collapsed}>
      <h1 className="sr-only" id={titleId}>{title}</h1>
      {collapsed ? (
        <button
          aria-label={labels.expand}
          className="sidebar-collapse-button"
          onClick={() => selection.setCollapsed(false)}
          type="button"
        >
          <PanelLeftOpen aria-hidden="true" size={18} />
        </button>
      ) : (
        <>
          {showTabs ? (
            <div className="resource-tabs" role="tablist">
              <button
                aria-selected={tab === "personal"}
                data-active={tab === "personal"}
                onClick={() => selection.setTab("personal")}
                role="tab"
                type="button"
              >
                <User aria-hidden="true" size={14} />
                {labels.personalTab}
              </button>
              <button
                aria-selected={tab === "global"}
                data-active={tab === "global"}
                onClick={() => selection.setTab("global")}
                role="tab"
                type="button"
              >
                <Building2 aria-hidden="true" size={14} />
                {labels.globalTab}
              </button>
            </div>
          ) : null}

          <div className="resource-sidebar-search">
            <Search aria-hidden="true" size={15} />
            <input
              aria-label={labels.searchLabel}
              onChange={(event) => selection.setQuery(event.target.value)}
              placeholder={labels.searchPlaceholder}
              type="search"
              value={query}
            />
            <button
              aria-label={labels.create}
              className="sidebar-collapse-button"
              disabled={createDisabled}
              onClick={onCreate}
              type="button"
            >
              <Plus aria-hidden="true" size={18} />
            </button>
            <button
              aria-label={labels.collapse}
              className="sidebar-collapse-button"
              onClick={() => selection.setCollapsed(true)}
              type="button"
            >
              <PanelLeftClose aria-hidden="true" size={18} />
            </button>
          </div>

          {visibleItems.length === 0 ? (
            <p className="empty-state">{query.trim() ? labels.noResults : labels.empty}</p>
          ) : (
            <ul className="resource-sidebar-list">
              {visibleItems.map((item) => (
                <ResourceSidebarRow
                  active={item.id === selectedItem?.id}
                  item={item}
                  key={item.id}
                  labels={labels}
                  onSelect={selection.selectItem}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </aside>
  );
}

type ResourceSidebarRowProps = {
  active: boolean;
  item: ResourceItem;
  labels: ResourceSidebarLabels;
  onSelect: (id: string) => void;
};

function ResourceSidebarRow({ active, item, labels, onSelect }: ResourceSidebarRowProps) {
  return (
    <li>
      <button
        aria-label={labels.openItem(item.name)}
        className="resource-sidebar-list-item"
        data-active={active}
        onClick={() => onSelect(item.id)}
        type="button"
      >
        <span className="resource-sidebar-list-item__name">{item.name}</span>
        <span className="resource-status-badge">
          {item.isActive ? labels.activeBadge : labels.inactiveBadge}
        </span>
      </button>
    </li>
  );
}
