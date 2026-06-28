/**
 * Navigation + route model — the single source for the sidebar groups and the
 * router. Groups follow DECISIONS #5: "Monitor / Evidence / Reference" — the
 * group is "Evidence", never "Trust". Icons are the mockup's unicode glyphs.
 */
export interface NavItem {
  /** URL slug (hash route) and stable key. */
  path: string;
  label: string;
  /** Topbar title (matches the mockup `titles` map). */
  title: string;
  icon: string;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Monitor",
    items: [
      { path: "overview", label: "Overview", title: "Overview", icon: "▤" },
      { path: "strategies", label: "Strategies", title: "Strategies", icon: "⇡" },
      { path: "conditions", label: "Conditions", title: "Conditions", icon: "◵" },
      { path: "data", label: "Data & Market", title: "Data & Market", icon: "◍" },
    ],
  },
  {
    label: "Evidence",
    items: [
      { path: "provenance", label: "Provenance", title: "Provenance", icon: "◈" },
      { path: "catalog", label: "Feature Catalog", title: "Feature Catalog", icon: "≣" },
      { path: "ledger", label: "Trial Registry", title: "Trial Registry", icon: "▥" },
    ],
  },
  {
    label: "Reference",
    items: [
      { path: "explain", label: "Explanations", title: "Explanations", icon: "✶" },
    ],
  },
];

export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);

export const DEFAULT_PATH = "overview";
