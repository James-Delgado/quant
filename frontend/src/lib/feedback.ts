/**
 * Feedback issue construction — the frontend half of E1-M6.
 *
 * Kept in lockstep with the Python service layer (`src/quant/console/feedback.py`):
 * the same `{title, type, severity, description, panel, build_sha, timestamp,
 * app_version}` payload and the same Markdown body, so a console-filed issue and
 * a (future E2) `POST /feedback` issue read identically. E1 submits with no
 * backend by opening the pre-filled `issues/new` URL in a new tab; the captured
 * context travels in the issue body (PRD §6, DECISIONS #11).
 */

export type FeedbackType = "bug" | "idea" | "data";
export type FeedbackSeverity = "low" | "med" | "high";

export const FEEDBACK_TYPES: readonly FeedbackType[] = ["bug", "idea", "data"];
export const FEEDBACK_SEVERITIES: readonly FeedbackSeverity[] = ["low", "med", "high"];

/** The `feedback` label every reported issue carries (DECISIONS #11). */
export const FEEDBACK_LABEL = "feedback";

/** Commit/issue links resolve here (DECISIONS #5; mirrors sources.DEFAULT_REPO_URL). */
export const REPO_URL = "https://github.com/James-Delgado/quant";

/** The "Report an issue" payload — modal fields + auto-captured context. */
export interface FeedbackReport {
  title: string;
  type: FeedbackType;
  severity: FeedbackSeverity;
  description: string;
  /** Current panel/route title (the "where"). */
  panel: string;
  /** Build SHA from `@/lib/utils` (the "which build"). */
  buildSha: string;
  /** ISO timestamp captured at submit. */
  timestamp: string;
  /** App version from package.json (the "which version"). */
  appVersion: string;
}

/** Markdown issue body — description + the auto-captured context block. */
export function buildIssueBody(r: FeedbackReport): string {
  return [
    `**Type:** ${r.type} · **Severity:** ${r.severity}`,
    "",
    r.description.trim(),
    "",
    "---",
    "",
    "**Context** (auto-captured)",
    "",
    `- Panel: ${r.panel}`,
    `- Build: ${r.buildSha}`,
    `- App version: ${r.appVersion}`,
    `- Reported: ${r.timestamp}`,
    "",
    '_Submitted via the Research Console "Report an issue" button._',
  ].join("\n");
}

/** A pre-filled `issues/new` URL: title + body + the `feedback` label. */
export function buildIssueUrl(r: FeedbackReport, repoUrl: string = REPO_URL): string {
  const params = new URLSearchParams({
    title: r.title.trim(),
    body: buildIssueBody(r),
    labels: FEEDBACK_LABEL,
  });
  return `${repoUrl}/issues/new?${params.toString()}`;
}
