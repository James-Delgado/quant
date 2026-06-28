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
    expect(parsed.searchParams.get("title")).toBe("Sparkline renders off-by-one");
    expect(parsed.searchParams.get("body")).toContain("Panel: Overview");
  });

  it("respects a repo override", () => {
    const url = buildIssueUrl(REPORT, "https://github.com/acme/widgets");
    expect(url.startsWith("https://github.com/acme/widgets/issues/new?")).toBe(true);
  });
});
