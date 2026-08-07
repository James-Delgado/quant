/**
 * Report-an-issue entry point (E1-M6, PRD §6).
 *
 * The UI surface is the button + this capture modal ONLY — there is NO
 * user-facing tracker panel (DECISIONS #11). The modal collects
 * {title, type, severity, description} and auto-captures {panel, build_sha,
 * timestamp, app_version}. Submission rides the E2-M4 static↔api flag:
 * static mode opens a pre-filled `feedback`-labeled GitHub issue in a new tab
 * (E1's no-backend path); api mode POSTs to the E2 service's `/feedback`
 * (one click, server-side token), carrying the optional bearer token and
 * degrading to the pre-filled-URL path on a 401.
 *
 * A11y: the dialog is `role="dialog" aria-modal`, labelled by its heading, opens
 * with focus on the first field, traps Tab, closes on Esc / Cancel / backdrop,
 * and restores focus to the trigger — reusing the keyboard discipline from the
 * E1-M5 InfoTip/StatePanel work.
 */
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { NAV_ITEMS, DEFAULT_PATH } from "@/nav";
import { APP_VERSION, BUILD_SHA } from "@/lib/utils";
import { API_BASE, API_TOKEN, DATA_SOURCE } from "@/lib/dataClient";
import {
  buildIssueUrl,
  FeedbackAuthError,
  FEEDBACK_SEVERITIES,
  FEEDBACK_TYPES,
  submitFeedback,
  type FeedbackReport,
  type FeedbackSeverity,
  type FeedbackType,
} from "@/lib/feedback";

const TOAST_MS = 3600;

export function ReportButton() {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [type, setType] = useState<FeedbackType>("bug");
  const [severity, setSeverity] = useState<FeedbackSeverity>("med");
  const [description, setDescription] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const triggerRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const firstFieldRef = useRef<HTMLInputElement>(null);
  const headingId = useId();

  // Current panel = the active route's title (same derivation as AppShell).
  const slug = location.pathname.replace(/^\//, "") || DEFAULT_PATH;
  const panel = NAV_ITEMS.find((i) => i.path === slug)?.title ?? "Overview";

  const close = useCallback(() => {
    setOpen(false);
    triggerRef.current?.focus();
  }, []);

  // On open: focus the first field. Restore-on-close is handled in `close()`.
  useEffect(() => {
    if (open) firstFieldRef.current?.focus();
  }, [open]);

  // Esc to close + a simple focus trap while the dialog is open.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
        return;
      }
      if (e.key !== "Tab" || !dialogRef.current) return;
      const focusable = dialogRef.current.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);

  // Toast auto-dismiss.
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), TOAST_MS);
    return () => clearTimeout(t);
  }, [toast]);

  /** Static-mode path (and api-mode 401 fallback): pre-filled issue tab. */
  function openIssueTab(report: FeedbackReport, message: string) {
    window.open(buildIssueUrl(report), "_blank", "noopener,noreferrer");
    setOpen(false);
    triggerRef.current?.focus();
    setTitle("");
    setDescription("");
    setToast(message);
  }

  async function submit() {
    const report: FeedbackReport = {
      title,
      type,
      severity,
      description,
      panel,
      buildSha: BUILD_SHA,
      timestamp: new Date().toISOString(),
      appVersion: APP_VERSION,
    };

    if (DATA_SOURCE !== "api") {
      // Static mode (E1's no-backend path): open the pre-filled issue tab.
      openIssueTab(
        report,
        "Report opened in a new tab — submit it on GitHub to file the issue.",
      );
      return;
    }

    // Api mode (E2-M4): one-click server-side filing via POST /feedback.
    setBusy(true);
    try {
      const filed = await submitFeedback(report, {
        apiBase: API_BASE,
        token: API_TOKEN,
      });
      setOpen(false);
      triggerRef.current?.focus();
      setTitle("");
      setDescription("");
      setToast(
        filed.issueNumber !== null
          ? `Issue #${filed.issueNumber} filed on GitHub.`
          : "Issue filed on GitHub.",
      );
    } catch (error: unknown) {
      if (error instanceof FeedbackAuthError) {
        // Graceful 401 degrade (E2-M3 discovery note): fall back to the
        // pre-filled-URL path so the report is never lost.
        openIssueTab(
          report,
          "API auth failed — opened a pre-filled issue in a new tab instead.",
        );
      } else {
        // Honest failure (METHODOLOGY §9): keep the modal (and the typed
        // report) intact so the user can retry. Refocus the dialog — the
        // disabled in-flight Submit dropped focus to <body>, outside the trap.
        const detail = error instanceof Error ? error.message : String(error);
        setToast(`Failed to file the issue: ${detail}`);
        firstFieldRef.current?.focus();
      }
    } finally {
      setBusy(false);
    }
  }

  const submitDisabled = title.trim().length === 0 || busy;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="report-btn"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        ＋ Report an issue
      </button>

      {open && (
        // Backdrop click-to-dismiss is a mouse-only convenience; keyboard users
        // already dismiss via Escape (handled on the dialog, see the keydown
        // effect above) plus Cancel. Giving the backdrop an interactive role +
        // tabindex would wrongly put it in the tab order and announce it as a
        // control, which is worse a11y — so suppress the generic handler rules here.
        // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
        <div
          className="modal-bg open"
          onClick={(e) => {
            if (e.target === e.currentTarget) close();
          }}
        >
          <div
            ref={dialogRef}
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby={headingId}
          >
            <h3 id={headingId}>Report an issue</h3>
            <div className="ctx">
              Context: {panel} · build {BUILD_SHA} · v{APP_VERSION}
            </div>

            <div className="field">
              <label htmlFor="r-title">Title</label>
              <input
                ref={firstFieldRef}
                id="r-title"
                type="text"
                placeholder="Short summary"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>

            <div className="frow">
              <div className="field">
                <label htmlFor="r-type">Type</label>
                <select
                  id="r-type"
                  value={type}
                  onChange={(e) => setType(e.target.value as FeedbackType)}
                >
                  {FEEDBACK_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label htmlFor="r-sev">Severity</label>
                <select
                  id="r-sev"
                  value={severity}
                  onChange={(e) =>
                    setSeverity(e.target.value as FeedbackSeverity)
                  }
                >
                  {FEEDBACK_SEVERITIES.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="field">
              <label htmlFor="r-desc">Description</label>
              <textarea
                id="r-desc"
                placeholder="What happened, and what you expected"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>

            <div className="mact">
              <button type="button" className="btn" onClick={close}>
                Cancel
              </button>
              <button
                type="button"
                className="btn primary"
                onClick={() => void submit()}
                disabled={submitDisabled}
              >
                {busy ? "Submitting…" : "Submit"}
              </button>
            </div>
          </div>
        </div>
      )}

      {toast && (
        <div className="toast show" role="status">
          {toast}
        </div>
      )}
    </>
  );
}
