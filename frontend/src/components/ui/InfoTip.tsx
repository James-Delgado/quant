import { useEffect, useRef, useState } from "react";

/**
 * Module-level bus so opening one tip dismisses any other (the mockup's
 * single-open behavior — keeps the UI uncluttered, DECISIONS #5/#9). We can't
 * lean on a document click for this because each trigger stops propagation to
 * survive its own outside-click listener; instead an opening tip broadcasts its
 * identity and every other instance closes.
 */
const tipBus = new EventTarget();

interface InfoTipProps {
  /** Definition shown on hover and pinned on click; also the accessible description. */
  tip: string;
  /** Accessible-name prefix for the trigger (the term being defined). */
  label: string;
}

/**
 * Inline ⓘ affordance, reused across panels (DECISIONS #9). Hover shows the tip
 * (CSS `::after`), click / Enter / Space pins it (`.open`), and Esc, an outside
 * click, or opening another tip dismisses it. The tip text is carried on
 * `aria-label` so assistive tech reads it, not only the decorative `::after`
 * bubble. Rendered as a real <button> (the a11y upgrade over the mockup's
 * <span>): natively focusable and Enter/Space-activated. Pure HTML/CSS — no
 * chart or data dependency, keeping the monochrome-chrome discipline.
 */
export function InfoTip({ tip, label }: InfoTipProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLButtonElement>(null);
  // Stable per-instance identity for the single-open bus.
  const idRef = useRef<object>({});

  // Close when another tip opens.
  useEffect(() => {
    function onOpenElsewhere(e: Event) {
      if ((e as CustomEvent).detail !== idRef.current) setOpen(false);
    }
    tipBus.addEventListener("open", onOpenElsewhere);
    return () => tipBus.removeEventListener("open", onOpenElsewhere);
  }, []);

  // Esc and outside-click dismiss only while pinned.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <button
      ref={ref}
      type="button"
      className={`info${open ? " open" : ""}`}
      data-tip={tip}
      aria-label={`${label}: ${tip}`}
      aria-expanded={open}
      onClick={(e) => {
        // Stop the document outside-click listener from immediately closing the
        // tip we are opening; cross-tip closing is handled by the bus instead.
        e.stopPropagation();
        const next = !open;
        setOpen(next);
        // Broadcast from the handler (not the reducer) so the other tips' close
        // is a normal batched state update, not a side effect during render.
        if (next) tipBus.dispatchEvent(new CustomEvent("open", { detail: idRef.current }));
      }}
    >
      i
    </button>
  );
}
