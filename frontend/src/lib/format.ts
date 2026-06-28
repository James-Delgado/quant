/**
 * Display formatters for the instrument panels.
 *
 * Pure functions — they take the neutral numeric series the service layer emits
 * and render them in the mockup's typographic conventions: monospace tabular
 * numerals, a true Unicode minus (U+2212) so signed numbers align, and a
 * leading "+" on gains. No business logic; presentation only (DECISIONS #1).
 */

const MINUS = "−"; // − , not the ASCII hyphen, so columns line up

/** Replace a leading ASCII "-" with a typographic minus. */
function sign(s: string): string {
  return s.startsWith("-") ? MINUS + s.slice(1) : s;
}

/** Fixed-decimal number with a typographic minus (e.g. -0.34 -> "−0.34"). */
export function fixed(value: number, dp = 2): string {
  return sign(value.toFixed(dp));
}

/** Signed fixed-decimal: always carries a leading + or − (e.g. "+0.42"). */
export function signedFixed(value: number, dp = 2): string {
  if (Number.isNaN(value)) return "—";
  const body = Math.abs(value).toFixed(dp);
  if (value > 0) return `+${body}`;
  if (value < 0) return `${MINUS}${body}`;
  return body;
}

/** Fraction -> signed percentage (0.418 -> "+41.8%", -0.21 -> "−21.0%"). */
export function signedPct(fraction: number, dp = 1): string {
  if (Number.isNaN(fraction)) return "—";
  const body = (Math.abs(fraction) * 100).toFixed(dp);
  if (fraction > 0) return `+${body}%`;
  if (fraction < 0) return `${MINUS}${body}%`;
  return `${body}%`;
}

/** Fraction -> unsigned percentage (0.997 -> "99.7%"). */
export function pct(fraction: number, dp = 1): string {
  return `${(fraction * 100).toFixed(dp)}%`;
}

/** CSS class for a value's sign: "gain" (>0), "loss" (<0), "" (0 / NaN). */
export function signClass(value: number): "gain" | "loss" | "" {
  if (Number.isNaN(value)) return "";
  if (value > 0) return "gain";
  if (value < 0) return "loss";
  return "";
}

/**
 * Compact OOS span from two ISO dates: ('2004-06-20', '2026-03-30') -> "’04–’26".
 * Returns "—" if either bound is missing.
 */
export function yearSpan(start: string | null, end: string | null): string {
  if (!start || !end) return "—";
  const a = start.slice(2, 4);
  const b = end.slice(2, 4);
  return `’${a}–’${b}`;
}
