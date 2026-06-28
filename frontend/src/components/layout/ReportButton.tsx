/**
 * Report-an-issue entry point. The full capture modal + GitHub-issue submission
 * is E1-M6 (PRD §6); this milestone renders the shell button only so the chrome
 * matches the mockup. Kept as a real, focusable button with an accessible label;
 * the disabled state communicates honestly that it is not yet wired (no faked
 * behavior — DECISIONS #5/#11).
 */
export function ReportButton() {
  return (
    <button
      type="button"
      className="report-btn"
      aria-label="Report an issue (available in a later milestone)"
      title="Report an issue — arrives in a later milestone"
      disabled
    >
      ＋ Report an issue
    </button>
  );
}
