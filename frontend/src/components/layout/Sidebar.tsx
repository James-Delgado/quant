import { NavLink } from "react-router-dom";
import { NAV_GROUPS } from "@/nav";
import { BUILD_SHA } from "@/lib/utils";
import { ReportButton } from "./ReportButton";

interface SidebarProps {
  /** Mobile slide-over open flag — applies the `.open` transform class. */
  open?: boolean;
  /** Called when a nav item is chosen (mobile slide-over closes itself). */
  onNavigate?: () => void;
}

/**
 * Left rail: brand, grouped navigation (Monitor / Evidence / Reference),
 * the Report-an-issue entry point, and the build footer. Parity with the
 * mockup `.side`. Nav items are real router links — focusable + keyboard
 * reachable for free.
 */
export function Sidebar({ open = false, onNavigate }: SidebarProps) {
  return (
    <aside className={open ? "side open" : "side"} id="sidebar">
      <div className="brand">
        <div className="nm">
          <span className="mk" aria-hidden="true" /> Research Console
        </div>
        <div className="sub">Quant Platform</div>
      </div>

      <nav className="nav" aria-label="Primary">
        {NAV_GROUPS.map((group) => (
          <div key={group.label}>
            <div className="grp">{group.label}</div>
            {group.items.map((item) => (
              <NavLink
                key={item.path}
                to={`/${item.path}`}
                className={({ isActive }) => (isActive ? "active" : undefined)}
                onClick={onNavigate}
              >
                <span className="ic" aria-hidden="true">
                  {item.icon}
                </span>{" "}
                {item.label}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      <ReportButton />
      <div className="ft">build {BUILD_SHA}</div>
    </aside>
  );
}
