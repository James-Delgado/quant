import { useEffect, useId, useRef, useState } from "react";
import { freshnessLines, STALE_LAG_DAYS } from "@/lib/freshness";
import type { ManifestSource } from "@/types/viewmodels";

interface FreshnessDisclosureProps {
  /** Export-run time (manifest.generated_at), ISO-8601 UTC; the staleness datum. */
  generatedAt?: string;
  /** Per-source artifact mtimes from the manifest. */
  sources: ManifestSource[];
}

/**
 * Accessible companion to the Topbar export stamp (E1-M2-TOPBAR-FRESHNESS-
 * DISCLOSURE). The per-source mtimes previously rode on a native `title`
 * attribute that keyboard and screen-reader users could not reach (WCAG 1.3.1 /
 * 4.1.2). This renders them as real DOM in a focusable disclosure: a ⓘ button
 * (reusing the `.info` trigger; `aria-expanded` + `aria-controls`, Esc /
 * outside-click dismiss, mirroring InfoTip) toggling a `role="region"` popover.
 * A source lagging the export run by more than STALE_LAG_DAYS is flagged
 * "behind" — a factual lag, not a value claim. Honesty: "unknown" for a null
 * mtime, never a guessed time (DECISIONS #7).
 */
export function FreshnessDisclosure({
  generatedAt,
  sources,
}: FreshnessDisclosureProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const regionId = useId();
  const lines = freshnessLines(sources, generatedAt);
  const behindCount = lines.filter((l) => l.behind).length;

  // Esc and outside-click dismiss only while open (mirrors InfoTip).
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node))
        setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const label =
    behindCount > 0
      ? `Per-source data freshness, ${behindCount} behind the latest export`
      : "Per-source data freshness";

  return (
    <span className="fresh" ref={ref}>
      <button
        type="button"
        className={`info${open ? " open" : ""}${behindCount > 0 ? " warn" : ""}`}
        aria-label={label}
        aria-expanded={open}
        aria-controls={regionId}
        onClick={(e) => {
          // Stop the document outside-click listener from closing what we open.
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        i
      </button>
      {open && (
        <div
          id={regionId}
          className="fresh-pop"
          role="region"
          aria-label="Per-source data freshness"
        >
          <ul>
            {lines.map((l) => (
              <li key={l.source} className={l.behind ? "behind" : undefined}>
                <span className="fs-src">{l.source}</span>
                <span className="fs-meta">
                  <span className="fs-time">updated {l.stamp}</span>
                  {l.behind && (
                    <span
                      className="fs-badge"
                      title={`Updated more than ${STALE_LAG_DAYS} days before this export`}
                    >
                      behind
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </span>
  );
}
