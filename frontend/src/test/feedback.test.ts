import { describe, expect, it } from "vitest";
import {
  buildIssueBody,
  buildIssueUrl,
  FEEDBACK_LABEL,
  type FeedbackReport,
} from "@/lib/feedback";

const REPORT: FeedbackReport = {
  title: "Sparkline renders off-by-one",
  type: "bug",
  severity: "high",
  description: "The Overview sparkline starts a day late.",
  panel: "Overview",
  buildSha: "abc1234",
  timestamp: "2026-06-28T18:30:00.000Z",
  appVersion: "0.0.0",
};

describe("buildIssueBody", () => {
  it("carries the user fields and the auto-captured context", () => {
    const body = buildIssueBody(REPORT);
    expect(body).toContain("**Type:** bug · **Severity:** high");
    expect(body).toContain("sparkline starts a day late");
    expect(body).toContain("- Panel: Overview");
    expect(body).toContain("- Build: abc1234");
    expect(body).toContain("- App version: 0.0.0");
    expect(body).toContain("- Reported: 2026-06-28T18:30:00.000Z");
  });
});

describe("buildIssueUrl", () => {
  it("builds a prefilled, feedback-labeled issues/new URL", () => {
    const url = buildIssueUrl(REPORT);
    const parsed = new URL(url);
    expect(parsed.pathname.endsWith("/issues/new")).toBe(true);
    expect(parsed.searchParams.get("labels")).toBe(FEEDBACK_LABEL);
    expect(parsed.searchParams.get("title")).toBe(
      "Sparkline renders off-by-one",
    );
    expect(parsed.searchParams.get("body")).toContain("Panel: Overview");
  });

  it("respects a repo override", () => {
    const url = buildIssueUrl(REPORT, "https://github.com/acme/widgets");
    expect(url.startsWith("https://github.com/acme/widgets/issues/new?")).toBe(
      true,
    );
  });
});

// ── Api-mode submission (E2-M4) ──────────────────────────────────────────────

import { afterEach, vi } from "vitest";
import {
  FeedbackAuthError,
  FeedbackSubmitError,
  submitFeedback,
  toApiPayload,
} from "@/lib/feedback";

afterEach(() => vi.unstubAllGlobals());

function stubFetchOnce(body: unknown, ok = true, status = 201) {
  const spy = vi.fn(async () => ({ ok, status, json: async () => body }));
  vi.stubGlobal("fetch", spy);
  return spy;
}

describe("toApiPayload", () => {
  it("maps the report to the Python dataclass's snake_case fields", () => {
    expect(toApiPayload(REPORT)).toEqual({
      title: REPORT.title,
      type: "bug",
      severity: "high",
      description: REPORT.description,
      panel: "Overview",
      build_sha: "abc1234",
      timestamp: "2026-06-28T18:30:00.000Z",
      app_version: "0.0.0",
    });
  });
});

describe("submitFeedback", () => {
  it("POSTs the snake_case payload and returns the filed issue", async () => {
    const spy = stubFetchOnce({
      issue_url: "https://github.com/James-Delgado/quant/issues/42",
      issue_number: 42,
      promoted: false,
    });
    const result = await submitFeedback(REPORT, {
      apiBase: "http://127.0.0.1:8000",
    });
    expect(result).toEqual({
      issueUrl: "https://github.com/James-Delgado/quant/issues/42",
      issueNumber: 42,
    });
    expect(spy).toHaveBeenCalledTimes(1);
    const [url, init] = spy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/feedback");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual(toApiPayload(REPORT));
    // No token configured → no Authorization header.
    expect(init.headers).not.toHaveProperty("Authorization");
  });

  it("carries the bearer token when configured", async () => {
    const spy = stubFetchOnce({ issue_url: "x", issue_number: null });
    await submitFeedback(REPORT, { apiBase: "http://a", token: "tok" });
    const [, init] = spy.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.headers).toHaveProperty("Authorization", "Bearer tok");
  });

  it("throws FeedbackAuthError on 401 (the graceful-degrade signal)", async () => {
    stubFetchOnce({ detail: "missing bearer token" }, false, 401);
    await expect(
      submitFeedback(REPORT, { apiBase: "http://a" }),
    ).rejects.toBeInstanceOf(FeedbackAuthError);
  });

  it("throws FeedbackSubmitError with the status on other failures", async () => {
    stubFetchOnce({ detail: "GitHub issue creation failed" }, false, 502);
    await expect(
      submitFeedback(REPORT, { apiBase: "http://a" }),
    ).rejects.toMatchObject({ name: "FeedbackSubmitError", status: 502 });
  });

  it("throws FeedbackSubmitError when the API is unreachable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    await expect(
      submitFeedback(REPORT, { apiBase: "http://a" }),
    ).rejects.toBeInstanceOf(FeedbackSubmitError);
  });
});
