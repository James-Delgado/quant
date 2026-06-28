import { Navigate, Route, Routes } from "react-router-dom";
import type { ComponentType } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Placeholder } from "@/pages/Placeholder";
import { Overview } from "@/pages/Overview";
import { Strategies } from "@/pages/Strategies";
import { Conditions } from "@/pages/Conditions";
import { NAV_ITEMS, DEFAULT_PATH } from "@/nav";

/**
 * Route table. The shell wraps every panel; each nav slug renders a panel body.
 * E1-M3 lands the three Monitor panels (Overview, Strategies, Conditions) as
 * real pages; the remaining slugs stay scaffold placeholders until E1-M4/M5
 * swap them in — without touching the shell.
 */
const PANELS: Record<string, ComponentType> = {
  overview: Overview,
  strategies: Strategies,
  conditions: Conditions,
};

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to={`/${DEFAULT_PATH}`} replace />} />
        {NAV_ITEMS.map((item) => {
          const Panel = PANELS[item.path];
          return (
            <Route
              key={item.path}
              path={item.path}
              element={Panel ? <Panel /> : <Placeholder slug={item.path} />}
            />
          );
        })}
        <Route path="*" element={<Navigate to={`/${DEFAULT_PATH}`} replace />} />
      </Route>
    </Routes>
  );
}
