interface TopbarProps {
  title: string;
  /** Export freshness ("updated …"), pulled from the real data_status asof. */
  asof?: string;
  onMenu: () => void;
}

/**
 * Top bar: menu toggle (mobile only), current panel title, an honest
 * "live execution · not connected" status (DECISIONS #7 — no faked live data),
 * and the export freshness stamp.
 */
export function Topbar({ title, asof, onMenu }: TopbarProps) {
  return (
    <header className="top">
      <button
        type="button"
        className="menu-btn"
        aria-label="Toggle navigation"
        aria-controls="sidebar"
        onClick={onMenu}
      >
        ☰
      </button>
      <span className="ttl">{title}</span>
      <span className="sp" />
      <span className="dotlive">
        <i aria-hidden="true" /> live execution · not connected
      </span>
      <span className="meta">{asof ? `data exported ${asof}` : ""}</span>
    </header>
  );
}
