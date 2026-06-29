import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";

/**
 * Accessible horizontal-scroll wrapper for a dense table inside `.panel.flush`
 * (E1-M5 a11y/responsive pass). At narrow widths the flush panel's
 * `overflow: hidden` clipped right-hand columns (Verdict / Commit / OOS) at 320px;
 * wrapping the table in a focusable labelled scroll region keeps every column
 * reachable while the page itself never overflows. This is the standard
 * "region + tabindex" responsive-table pattern: keyboard users can Tab to the
 * region and arrow-scroll, and the focus ring uses the shared `[tabindex]` style.
 *
 * Keyboard users get the focus ring as the discoverability cue, but mouse/touch
 * users had none (E1-M5-TABLE-SCROLL-AFFORDANCE). This component measures the
 * real overflow (`scrollWidth > clientWidth`) and scroll position, then toggles
 * `data-scroll-left` / `data-scroll-right` on the wrapper so CSS fades in an edge
 * cue ONLY on the side that still has off-screen columns. The cue is honest: it
 * reflects measured geometry, never a guess, and disappears entirely when the
 * table fits or once that edge is reached (METHODOLOGY §9).
 */
export function TableScroll({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });

  const measure = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    // Sub-pixel rounding can leave a ~1px residual at a true edge; tolerate it so
    // a fully-scrolled or exactly-fitting table shows no spurious cue.
    const maxScrollLeft = el.scrollWidth - el.clientWidth;
    const left = el.scrollLeft > 1;
    const right = el.scrollLeft < maxScrollLeft - 1;
    setEdges((prev) =>
      prev.left === left && prev.right === right ? prev : { left, right },
    );
  }, []);

  // Measure synchronously after layout so the cue is correct on first paint.
  useLayoutEffect(measure, [measure, children]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.addEventListener("scroll", measure, { passive: true });
    window.addEventListener("resize", measure);
    // ResizeObserver catches content-driven size changes (async data load,
    // font swap). Guarded — jsdom/older runtimes may not provide it; the scroll
    // + window-resize listeners keep the cue correct without it.
    const observer =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(measure)
        : null;
    observer?.observe(el);
    return () => {
      el.removeEventListener("scroll", measure);
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, [measure]);

  return (
    <div
      className="table-scroll-wrap"
      data-scroll-left={edges.left || undefined}
      data-scroll-right={edges.right || undefined}
    >
      {/* A labelled `role="region"` scroll container is deliberately focusable so
          keyboard-only users can Tab in and arrow-scroll the overflowing table
          (WAI-ARIA scrollable-region pattern). jsx-a11y flags non-interactive
          tabindex generically; this is the documented exception, not an oversight.
          Block disable (not -next-line) so the multi-line opening tag stays
          covered regardless of formatter line-wrapping. */}
      {/* eslint-disable jsx-a11y/no-noninteractive-tabindex */}
      <div
        ref={scrollRef}
        className="table-scroll"
        role="region"
        aria-label={label}
        tabIndex={0}
      >
        {children}
      </div>
      {/* eslint-enable jsx-a11y/no-noninteractive-tabindex */}
      {/* Decorative edge cues — opacity is driven by the wrapper data-attributes
          in CSS; aria-hidden so AT never announces them. */}
      <span
        className="table-scroll-cue table-scroll-cue--left"
        aria-hidden="true"
      />
      <span
        className="table-scroll-cue table-scroll-cue--right"
        aria-hidden="true"
      />
    </div>
  );
}
