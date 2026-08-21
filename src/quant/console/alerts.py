"""Staleness / gap / drift / regime-change alerting (E4-M2).

E4-M1 gave the console honest *state*: per-ingestor SLA verdicts, lake gap
reports, and (from E1) per-feature drift verdicts. This module turns breaches
of that state into *alerts* — the E4 PRD §4 M2 deliverable, including the
regime-change alerting folded in from the superseded roadmap §C5.

Design
------
* **Pure evaluation core.** Every ``*_alert(s)`` producer is a deterministic
  function over already-computed view-models (plus the VIX series for the
  regime axis) — no lake access, so the seeded-breach acceptance tests need no
  real data. ``evaluate_alerts`` composes the four producers.
* **One source of truth per signal (METHODOLOGY §6).** Staleness consumes the
  C1-M3 SLA verdicts verbatim (E4 never re-judges freshness); drift consumes
  the E1 feature-monitor ``stability`` verdicts (whose z / PSI thresholds are
  pinned in ``sources.py``); the regime axis reuses
  :class:`quant.backtest.regimes.VIXThresholdDetector` with its pinned,
  documented 15/25 thresholds. This module pins only what is new: severity
  mapping, gap materiality windows, and the systemic-drift escalation.
* **Materiality before significance (METHODOLOGY §10) — the alert-fatigue
  guard.** Severity has exactly two levels (``warning`` / ``critical``). Gap
  alerts fire only for *recent* gaps — the lake's known historical gap backlog
  (e.g. the 564 Alpaca sessions tracked by C1-ALPACA-GAP-AUDIT) is a panel /
  audit concern, not a daily re-alert. Feature drift raises ONE aggregate
  alert per evaluation, never one per feature. Regime-change alerts compare
  only the two most recent observations, so daily cadence bounds them to at
  most one per day.
* **Honest degrades (METHODOLOGY §9).** An input that cannot be checked
  produces an explicit could-not-check *note*, never a fabricated all-clear.

Channel decision (the DECISIONS.md "Alert channel (E4): TBD" item)
------------------------------------------------------------------
The pinned channel is the **log channel**: structured lines to stderr plus a
non-zero exit from ``python -m quant.console alerts`` — the same
cron-mail-on-stderr contract C1-M3's freshness monitor already established.
It needs zero new secrets or infrastructure; richer channels (email / push)
are later :class:`AlertChannel` implementations, not a redesign. Recorded in
``docs/project-e/DECISIONS.md``.
"""

from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Sequence
from typing import Protocol, TextIO

import pandas as pd

from quant.backtest.regimes import VIXThresholdDetector
from quant.console import viewmodels as vm

# ── Alert vocabulary (pinned; METHODOLOGY §1) ────────────────────────────────
# Kinds mirror the E4 PRD §4 M2 breach list; severities are deliberately only
# two — more levels would dilute what "critical" means (§10 fatigue guard).
KIND_STALENESS = "staleness"
KIND_GAP = "gap"
KIND_DRIFT = "drift"
KIND_REGIME_CHANGE = "regime_change"

SEVERITY_WARNING = "warning"
SEVERITY_CRITICAL = "critical"
# Deterministic ordering for the view / channel: critical first.
_SEVERITY_ORDER = {SEVERITY_CRITICAL: 0, SEVERITY_WARNING: 1}

# ── Pinned thresholds (METHODOLOGY §1) ───────────────────────────────────────
# Staleness severity per C1-M3 SLA state: a stale feed still has *some* recent
# data (degraded inference); a missing feed has none (no honest inference).
STALENESS_SEVERITY: dict[str, str] = {
    "stale": SEVERITY_WARNING,
    "missing": SEVERITY_CRITICAL,
}
# Gap materiality window: only gaps inside the trailing N NYSE sessions of the
# dataset's observed span raise an alert. 21 sessions ≈ one trading month = the
# longest label horizon in the B1 target catalog, so any gap that can touch a
# live label/feature window alerts; older gaps stay a panel + audit-task
# concern (§10 — re-alerting daily on a known historical backlog is fatigue).
GAP_ALERT_RECENT_SESSIONS = 21
# Escalation: a gap within the trailing week of sessions means the *current*
# ingest pipeline silently lost a session — same class as a missing feed.
GAP_CRITICAL_RECENT_SESSIONS = 5
# Systemic-drift escalation: one drifting feature is a feature-level concern
# (warning); a quarter of the monitored set drifting at once is a
# distribution-level shift (critical).
DRIFT_CRITICAL_FRACTION = 0.25
# Cap the feature names listed in the aggregate drift message (count is exact).
MAX_DRIFT_FEATURES_LISTED = 10
# Entering this VIX regime escalates the regime-change alert to critical.
REGIME_CRITICAL_LABEL = "high_vol"


# ── Producers (pure; one per breach kind) ────────────────────────────────────


def staleness_alerts(sla: Sequence[vm.SlaFeedStatus]) -> list[vm.AlertItem]:
    """One alert per non-fresh C1-M3 SLA verdict (E4 consumes, never re-judges)."""
    alerts = []
    for status in sla:
        severity = STALENESS_SEVERITY.get(status.state)
        if severity is None:  # "fresh" (or an unknown state — never invent an alert)
            continue
        alerts.append(
            vm.AlertItem(
                kind=KIND_STALENESS,
                severity=severity,
                subject=status.feed,
                message=f"SLA breach ({status.state}): {status.detail}",
            )
        )
    return alerts


def _sessions_back(end: dt.date, n: int) -> dt.date:
    """The NYSE session *n* sessions before the last session on/≤ *end*."""
    from quant.utils.calendar import trading_days

    window = trading_days(end - dt.timedelta(days=10 + 2 * n), end)
    if not window:
        return end
    return window[max(len(window) - 1 - n, 0)]


def gap_alerts(
    gaps: Sequence[vm.LakeGapReport],
) -> tuple[list[vm.AlertItem], list[str]]:
    """Alert on gaps inside the trailing materiality window; note the unchecked.

    Operates on the E4-M1 :class:`~quant.console.viewmodels.LakeGapReport`s.
    The surfaced ``gap_dates`` are capped to the most recent
    ``health.MAX_GAP_DATES_SURFACED`` — sufficient here, because any gap inside
    the trailing ``GAP_ALERT_RECENT_SESSIONS`` sessions is by construction
    among the most recent (if more than the cap fall inside the window, the
    listed ones still do).
    """
    alerts: list[vm.AlertItem] = []
    notes: list[str] = []
    for report in gaps:
        if report.n_gaps is None or report.window_end is None:
            notes.append(f"Gap check unavailable for {report.feed} — not evaluated.")
            continue
        if report.n_gaps == 0:
            continue
        window_end = dt.date.fromisoformat(report.window_end)
        warn_cutoff = _sessions_back(window_end, GAP_ALERT_RECENT_SESSIONS)
        crit_cutoff = _sessions_back(window_end, GAP_CRITICAL_RECENT_SESSIONS)
        recent = [d for d in report.gap_dates if dt.date.fromisoformat(d) >= warn_cutoff]
        if not recent:
            continue  # historical backlog only — panel/audit concern, not an alert
        is_critical = any(dt.date.fromisoformat(d) >= crit_cutoff for d in recent)
        alerts.append(
            vm.AlertItem(
                kind=KIND_GAP,
                severity=SEVERITY_CRITICAL if is_critical else SEVERITY_WARNING,
                subject=report.feed,
                message=(
                    f"{len(recent)} missing session(s) in the last "
                    f"{GAP_ALERT_RECENT_SESSIONS} sessions: {', '.join(sorted(recent))}"
                ),
            )
        )
    return alerts, notes


def drift_alert(
    features: Sequence[vm.FeatureCard],
) -> tuple[vm.AlertItem | None, list[str]]:
    """ONE aggregate alert over the E1 feature-monitor verdicts (§10 fatigue guard).

    A feature counts as drifting when its monitor ``stability`` verdict says so
    — the z / PSI thresholds behind that verdict are pinned in ``sources.py``
    and are not re-judged here (§6). With no monitored features at all (monitor
    not wired), the honest answer is a could-not-check note.
    """
    monitored = [f for f in features if f.stability is not None]
    if not monitored:
        return None, ["Feature monitor not wired — drift not evaluated."]
    drifting = sorted(f.name for f in monitored if f.stability == "drifting")
    if not drifting:
        return None, []
    fraction = len(drifting) / len(monitored)
    listed = ", ".join(drifting[:MAX_DRIFT_FEATURES_LISTED])
    if len(drifting) > MAX_DRIFT_FEATURES_LISTED:
        listed += ", …"
    return (
        vm.AlertItem(
            kind=KIND_DRIFT,
            severity=(
                SEVERITY_CRITICAL if fraction >= DRIFT_CRITICAL_FRACTION else SEVERITY_WARNING
            ),
            subject="feature catalog",
            message=(f"{len(drifting)} of {len(monitored)} monitored features drifting: {listed}"),
        ),
        [],
    )


def regime_change_alert(
    vix: pd.Series | None,
) -> tuple[vm.AlertItem | None, list[str]]:
    """Alert when the two most recent VIX observations straddle a regime bound.

    Reuses :class:`VIXThresholdDetector` with its pinned defaults (15/25) — the
    same thresholds the conditions panel and the Phase 4A regime harness use,
    so "the regime changed" can never mean different things in different
    surfaces (§6). Feed *staleness* is deliberately not judged here — a late
    VIXCLS series already raises its own staleness alert; this producer only
    answers "did the regime flip between the last two observations?".
    """
    if vix is None or len(vix.dropna()) < 2:
        return None, ["VIX series unavailable — regime-change not evaluated."]
    tail = vix.dropna().iloc[-2:]
    labels = VIXThresholdDetector(vix_series=tail).label(tail.index)
    prev_label, curr_label = labels.iloc[0], labels.iloc[1]
    if prev_label == curr_label:
        return None, []
    return (
        vm.AlertItem(
            kind=KIND_REGIME_CHANGE,
            severity=(
                SEVERITY_CRITICAL if curr_label == REGIME_CRITICAL_LABEL else SEVERITY_WARNING
            ),
            subject="vol regime (VIX)",
            # The observation dates make a lagging series self-evident: an
            # operator can see at a glance whether the flip is today's or an
            # old one surfacing late (its lateness alerts separately via SLA).
            message=(
                f"VIX {tail.iloc[0]:.1f} → {tail.iloc[1]:.1f} "
                f"({tail.index[0].date().isoformat()} → {tail.index[1].date().isoformat()}): "
                f"{prev_label} → {curr_label}"
            ),
        ),
        [],
    )


# ── Composition ──────────────────────────────────────────────────────────────


def evaluate_alerts(
    data_status: vm.DataStatusView,
    catalog: vm.CatalogView,
    vix: pd.Series | None,
) -> tuple[list[vm.AlertItem], list[str]]:
    """Run all four producers; alerts sorted critical-first, then kind/subject."""
    alerts = list(staleness_alerts(data_status.sla))
    notes: list[str] = []
    if not data_status.sla:
        # E4-M1's reader already carries the explanatory degrade note; repeat it
        # here so the alerts surface is self-describing (§9).
        notes.append("SLA verdicts unavailable — staleness not evaluated.")
    gap_items, gap_notes = gap_alerts(data_status.gaps)
    alerts.extend(gap_items)
    notes.extend(gap_notes)
    drift_item, drift_notes = drift_alert(catalog.features)
    if drift_item is not None:
        alerts.append(drift_item)
    notes.extend(drift_notes)
    regime_item, regime_notes = regime_change_alert(vix)
    if regime_item is not None:
        alerts.append(regime_item)
    notes.extend(regime_notes)
    alerts.sort(key=lambda a: (_SEVERITY_ORDER.get(a.severity, 9), a.kind, a.subject))
    return alerts, notes


# ── Channel (the pinned E4 alert channel + its extension seam) ───────────────


class AlertChannel(Protocol):
    """Anything that can deliver an evaluated :class:`AlertsView`."""

    def emit(self, view: vm.AlertsView) -> None: ...


class LogChannel:
    """The pinned E4 channel: structured lines to a stream (stderr by default).

    Under the daily cron this is the same delivery contract as the C1-M3
    freshness monitor — stderr output plus the CLI's non-zero exit triggers
    cron's mail-on-output. Email / push are future ``AlertChannel``
    implementations behind the same ``emit`` seam.
    """

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stderr

    def emit(self, view: vm.AlertsView) -> None:
        print(format_report(view), file=self._stream)


def format_report(view: vm.AlertsView) -> str:
    """Render the alerts view for the log channel / CLI."""
    lines = [f"Alerts @ {view.asof}"]
    for a in view.alerts:
        lines.append(f"  [{a.severity.upper():>8}] {a.kind:<13} {a.subject}: {a.message}")
    for note in view.notes:
        lines.append(f"  [    NOTE] {note}")
    if not view.alerts:
        lines.append("  → NO ALERTS")
    else:
        n_crit = sum(1 for a in view.alerts if a.severity == SEVERITY_CRITICAL)
        lines.append(f"  → {len(view.alerts)} ALERT(S) ({n_crit} critical)")
    return "\n".join(lines)
