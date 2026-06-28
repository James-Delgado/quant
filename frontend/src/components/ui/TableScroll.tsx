import type { ReactNode } from "react";

/**
 * Accessible horizontal-scroll wrapper for a dense table inside `.panel.flush`
 * (E1-M5 a11y/responsive pass). At narrow widths the flush panel's
 * `overflow: hidden` clipped right-hand columns (Verdict / Commit / OOS) at 320px;
 * wrapping the table in a focusable labelled scroll region keeps every column
 * reachable while the page itself never overflows. This is the standard
 * "region + tabindex" responsive-table pattern: keyboard users can Tab to the
 * region and arrow-scroll, and the focus ring uses the shared `[tabindex]` style.
 */
export function TableScroll({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="table-scroll" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}
