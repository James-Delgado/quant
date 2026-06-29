import { utcStamp } from "@/lib/format";
import type { ManifestSource } from "@/types/viewmodels";
import { FreshnessDisclosure } from "./FreshnessDisclosure";

interface TopbarProps {
  title: string;
  /** Export-run time (manifest.generated_at), ISO-8601 UTC. */
  generatedAt?: string;
  /** Per-source artifact mtimes from the manifest, for the freshness tooltip. */
  sources?: ManifestSource[];
  onMenu: () => void;
}

/** "Trial Registry updated 2026-06-28 17:52 UTC" — "unknown" for a null mtime
 *  (never a guessed time; the manifest already degrades to null honestly). */
function sourceLine(s: ManifestSource): string {
  return `${s.source} updated ${s.modified_at ? utcStamp(s.modified_at) : "unknown"}`;
}

/**
 * Top bar: menu toggle (mobile only), current panel title, an honest
 * "live execution · not connected" status (DECISIONS #7 — no faked live data),
 * and the export freshness stamp sourced from the manifest (E1-M2-TOPBAR-
 * FRESHNESS). The stamp shows the export-run time; the adjacent ⓘ disclosure
 * (E1-M2-TOPBAR-FRESHNESS-DISCLOSURE) exposes each upstream artifact's mtime to
 * keyboard/screen-reader users and flags any source lagging the export run. The
 * native `title` is kept as a sighted-mouse-hover convenience.
 */
export function Topbar({ title, generatedAt, sources, onMenu }: TopbarProps) {
  const stamp = generatedAt ? utcStamp(generatedAt) : "";
  const tooltip = sources?.length
    ? sources.map(sourceLine).join("\n")
    : undefined;
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
      <span className="meta" title={stamp ? tooltip : undefined}>
        {stamp ? `data exported ${stamp}` : ""}
      </span>
      {stamp && sources?.length ? (
        <FreshnessDisclosure generatedAt={generatedAt} sources={sources} />
      ) : null}
    </header>
  );
}
