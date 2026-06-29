/**
 * Honest non-data states for a panel: a quiet loading line, an explicit error
 * surface, and an empty surface for a successful-but-empty export (DECISIONS #5
 * — never fake data; show the real status). Kept inside the `.ph` dashed frame
 * the scaffold introduced so empty/error reads as a deliberate state, not a
 * broken layout.
 */
import type { ReactNode } from "react";

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

/**
 * A successful export that simply contains nothing for this panel — e.g. the
 * roster is empty because no strategy checkpoints were present when the export
 * ran (a fresh clone before the data-prep step). Distinct from `ErrorState`: the
 * export succeeded; there is just nothing to show yet. Honest about *why* and
 * *how to fix it* without leaking internal file paths (DECISIONS #5/#7).
 */
export function EmptyState({
  label = "nothing to show",
  children,
}: {
  label?: string;
  children?: ReactNode;
}) {
  return (
    <div className="ph" role="status" aria-live="polite">
      <span className="pill idle">{label}</span>
      {children != null && <div style={{ marginTop: 8 }}>{children}</div>}
    </div>
  );
}
