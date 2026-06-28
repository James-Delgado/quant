/**
 * Honest non-data states for a panel: a quiet loading line and an explicit
 * error surface (DECISIONS #5 — never fake data; show the real status). Kept
 * inside the `.ph` dashed frame the scaffold introduced so empty/error reads
 * as a deliberate state, not a broken layout.
 */
export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="ph" role="status" aria-live="polite">
      <span className="pill idle">{label}</span>
    </div>
  );
}

export function ErrorState({ error }: { error: Error }) {
  return (
    <div className="ph" role="alert">
      <span className="pill bad">data unavailable</span>
      <div style={{ marginTop: 8 }}>
        This panel reads from the console export. {error.message}. Run{" "}
        <span className="mono">python -m quant.console export</span> and reload.
      </div>
    </div>
  );
}
