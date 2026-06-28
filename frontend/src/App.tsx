import { Navigate, Route, Routes } from "react-router-dom";
import type { ComponentType } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Placeholder } from "@/pages/Placeholder";
import { Overview } from "@/pages/Overview";
import { Strategies } from "@/pages/Strategies";
import { Portfolio } from "@/pages/Portfolio";
import { Conditions } from "@/pages/Conditions";
import { Provenance } from "@/pages/Provenance";
import { FeatureCatalog } from "@/pages/FeatureCatalog";
import { Ledger } from "@/pages/Ledger";
import { DataMarket } from "@/pages/DataMarket";
import { Explanations } from "@/pages/Explanations";
import { NAV_ITEMS, DEFAULT_PATH } from "@/nav";

/**
 * Route table. The shell wraps every panel; each nav slug renders a panel body.
 * E1-M3 landed the Monitor panels (Overview, Strategies, Conditions); E1-M4
 * lands the Evidence panels (Provenance, Feature Catalog, Trial Registry) plus
 * Data & Market; E1-M5 lands the Explanations reference panel. Every nav slug
 * now maps to a real page — the Placeholder remains only as the defensive
 * fallback for an unmapped slug.
 */
const PANELS: Record<string, ComponentType> = {
  overview: Overview,
  strategies: Strategies,
  portfolio: Portfolio,
  conditions: Conditions,
  data: DataMarket,
  provenance: Provenance,
  catalog: FeatureCatalog,
  ledger: Ledger,
  explain: Explanations,
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
        <Route
          path="*"
          element={<Navigate to={`/${DEFAULT_PATH}`} replace />}
        />
      </Route>
    </Routes>
  );
}
