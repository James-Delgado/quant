import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { NAV_ITEMS, DEFAULT_PATH } from "@/nav";
import { dataClient } from "@/lib/dataClient";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

/**
 * App frame: persistent sidebar + topbar with the routed panel rendered in the
 * scrolling content well. Owns two pieces of cross-panel state: the mobile
 * slide-over open flag and the export freshness stamp (loaded once from the
 * real data_status export — the thin data client wired end-to-end).
 */
export function AppShell() {
  const location = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const [asof, setAsof] = useState<string | undefined>();

  const slug = location.pathname.replace(/^\//, "") || DEFAULT_PATH;
  const title = NAV_ITEMS.find((i) => i.path === slug)?.title ?? "Overview";

  // Close the mobile nav whenever the route changes.
  useEffect(() => setNavOpen(false), [location.pathname]);

  // One real read through the data client, proving the static wiring works.
  useEffect(() => {
    const ctrl = new AbortController();
    dataClient
      .dataStatus(ctrl.signal)
      .then((d) => setAsof(d.asof))
      .catch(() => {
        /* shell tolerates a missing export; panels own their error states */
      });
    return () => ctrl.abort();
  }, []);

  return (
    <div className="app">
      {navOpen && (
        <div
          className="scrim"
          onClick={() => setNavOpen(false)}
          aria-hidden="true"
        />
      )}
      <Sidebar open={navOpen} onNavigate={() => setNavOpen(false)} />
      <div className="main">
        <Topbar title={title} asof={asof} onMenu={() => setNavOpen((o) => !o)} />
        <div className="content">
          <div className="maxw">
            <Outlet />
          </div>
        </div>
      </div>
    </div>
  );
}
