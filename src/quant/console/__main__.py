"""Console CLI entry point.

Canonical invocation (no reinstall needed):

    python -m quant.console export [--out DIR]
    python -m quant.console feedback promote <issue> [--priorities PATH]

``export`` runs every reader over the production sources and writes the static
JSON tree (default: ``src/quant/console/export/``). ``feedback promote`` reads a
``feedback``-labeled GitHub issue and appends it to ``docs/PRIORITIES.yaml`` as a
``FEEDBACK-<issue>`` task with a back-link (PRD §6, DECISIONS #11). A ``console``
console-script is also declared in ``pyproject.toml`` and activates on the next
editable install; until then use the module form above.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="console", description="Research console tools")
    sub = parser.add_subparsers(dest="command", required=True)

    export_parser = sub.add_parser("export", help="Write the static JSON export tree")
    export_parser.add_argument(
        "--out",
        default=None,
        help="Output directory (default: src/quant/console/export/)",
    )

    feedback_parser = sub.add_parser(
        "feedback", help="Issue-tracker tooling (promote a feedback issue to a task)"
    )
    feedback_sub = feedback_parser.add_subparsers(dest="feedback_command", required=True)
    promote_parser = feedback_sub.add_parser(
        "promote", help="Append a feedback GitHub issue to PRIORITIES.yaml as a task"
    )
    promote_parser.add_argument("issue", type=int, help="GitHub issue number to promote")
    promote_parser.add_argument(
        "--priorities",
        default=None,
        help="PRIORITIES.yaml path (default: repo docs/PRIORITIES.yaml)",
    )

    args = parser.parse_args(argv)

    if args.command == "export":
        # Imported here so `--help` does not trigger settings/credential loading.
        from quant.console.export import build_export, fanout_coverage, write_export
        from quant.console.sources import ConsoleSources

        # One sources instance shared by the write + the coverage probe. The
        # lake-backed feature monitor memoizes its panel build per instance, so
        # the second build_export() below reuses it instead of rebuilding the
        # full feature panel (no double full-panel cost).
        sources = ConsoleSources.default()
        written = write_export(out_dir=args.out, sources=sources)
        print(f"Wrote {len(written)} export files:")
        for path in written:
            print(f"  {path}")

        # Surface the per-strategy fan-out coverage so an empty/partial
        # Strategies-detail (M3) / Provenance (M4) fan-out is visible at the CLI,
        # not just in the warning log (E1-M2-EXPORT-DETAIL; METHODOLOGY §9).
        coverage = fanout_coverage(build_export(sources))
        print(f"Fan-out: {coverage.summary()}.")
        if not coverage.complete:
            print(
                "  WARNING: Strategies-detail (M3) / Provenance (M4) will be "
                "empty or partial — regenerate strategy checkpoints "
                '(see frontend/README.md § "Detail / provenance data prep").'
            )
        return 0

    if args.command == "feedback" and args.feedback_command == "promote":
        from quant.console import feedback

        kwargs = {}
        if args.priorities is not None:
            kwargs["priorities_path"] = args.priorities
        task = feedback.promote(args.issue, **kwargs)
        print(f"Promoted issue #{args.issue} → task {task.id} (rank {task.rank})")
        print(f"  {task.title}")
        print(f"  {task.issue_url}")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
