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
export const FEEDBACK_SEVERITIES: readonly FeedbackSeverity[] = [
  "low",
  "med",
  "high",
];

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
export function buildIssueUrl(
  r: FeedbackReport,
  repoUrl: string = REPO_URL,
): string {
  const params = new URLSearchParams({
    title: r.title.trim(),
    body: buildIssueBody(r),
    labels: FEEDBACK_LABEL,
  });
  return `${repoUrl}/issues/new?${params.toString()}`;
}

// ── Api-mode submission (E2-M4) ──────────────────────────────────────────────
//
// In api mode the modal swaps the two-click pre-filled-URL path for one click:
// `POST {apiBase}/feedback`, the server files the issue with its own token
// (never in the client). The wire payload is the SAME frozen contract, in the
// Python `FeedbackReport` dataclass's snake_case spelling.

/** The `POST /feedback` wire payload — snake_case per the Python dataclass. */
export function toApiPayload(r: FeedbackReport): Record<string, string> {
  return {
    title: r.title,
    type: r.type,
    severity: r.severity,
    description: r.description,
    panel: r.panel,
    build_sha: r.buildSha,
    timestamp: r.timestamp,
    app_version: r.appVersion,
  };
}

/** 401 from `POST /feedback` — the caller degrades to the pre-filled-URL path. */
export class FeedbackAuthError extends Error {
  constructor() {
    super("feedback API rejected the token (HTTP 401)");
    this.name = "FeedbackAuthError";
  }
}

/** Any other `POST /feedback` failure (4xx/5xx/network) — surfaced, not hidden. */
export class FeedbackSubmitError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "FeedbackSubmitError";
  }
}

/** The 201 body of `POST /feedback` (promotion fields omitted — not sent). */
export interface FeedbackApiResult {
  issueUrl: string;
  issueNumber: number | null;
}

/**
 * Submit the report to the E2 API. Throws `FeedbackAuthError` on 401 (so the
 * caller can fall back to `buildIssueUrl`) and `FeedbackSubmitError` on any
 * other failure. The bearer token is optional — with none configured
 * server-side the route is open under the localhost-only bind (E2-M3).
 */
export async function submitFeedback(
  r: FeedbackReport,
  opts: { apiBase: string; token?: string },
): Promise<FeedbackApiResult> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (opts.token) headers["Authorization"] = `Bearer ${opts.token}`;
  let res: Response;
  try {
    res = await fetch(`${opts.apiBase}/feedback`, {
      method: "POST",
      headers,
      body: JSON.stringify(toApiPayload(r)),
    });
  } catch (error: unknown) {
    const detail = error instanceof Error ? error.message : "network error";
    throw new FeedbackSubmitError(`feedback API unreachable: ${detail}`);
  }
  if (res.status === 401) throw new FeedbackAuthError();
  if (!res.ok) {
    throw new FeedbackSubmitError(
      `feedback API failed (HTTP ${res.status})`,
      res.status,
    );
  }
  const body = (await res.json()) as {
    issue_url: string;
    issue_number: number | null;
  };
  return { issueUrl: body.issue_url, issueNumber: body.issue_number };
}
