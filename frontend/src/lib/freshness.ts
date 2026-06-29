import { utcStamp } from "@/lib/format";
import type { ManifestSource } from "@/types/viewmodels";

/** A source is flagged "behind" when its artifact mtime lags the export-run
 *  time by more than this. Data is daily-cadence (PROJECT_ROADMAP §8), so a
 *  source not refreshed within a week of the export run is meaningfully behind.
 *  Pinned per METHODOLOGY §1. A null mtime is "unknown" and is NEVER flagged —
 *  we don't guess (DECISIONS #7 / METHODOLOGY §9). */
export const STALE_LAG_DAYS = 7;
const STALE_LAG_MS = STALE_LAG_DAYS * 24 * 60 * 60 * 1000;

export interface FreshnessLine {
  source: string;
  /** "2026-06-28 17:52 UTC" for a known mtime, else "unknown". */
  stamp: string;
  /** mtime lags generated_at by more than STALE_LAG_DAYS; false when unknown. */
  behind: boolean;
}

/**
 * Pure mapper from manifest sources to display rows: formats each mtime (or
 * "unknown" for null) and marks the row "behind" when it lags the export run by
 * more than the pinned threshold. A null mtime never counts as behind.
 */
export function freshnessLines(
  sources: ManifestSource[],
  generatedAt?: string,
): FreshnessLine[] {
  const genMs = generatedAt ? Date.parse(generatedAt) : NaN;
  return sources.map((s) => {
    const ms = s.modified_at ? Date.parse(s.modified_at) : NaN;
    const behind =
      Number.isFinite(ms) &&
      Number.isFinite(genMs) &&
      genMs - ms > STALE_LAG_MS;
    return {
      source: s.source,
      stamp: s.modified_at ? utcStamp(s.modified_at) : "unknown",
      behind,
    };
  });
}
