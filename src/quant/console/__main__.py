"""Console CLI entry point.

Canonical invocation (no reinstall needed):

    python -m quant.console export [--out DIR]

``export`` runs every reader over the production sources and writes the static
JSON tree (default: ``src/quant/console/export/``). A ``console`` console-script
is also declared in ``pyproject.toml`` and activates on the next editable
install; until then use the module form above.
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

    args = parser.parse_args(argv)

    if args.command == "export":
        # Imported here so `--help` does not trigger settings/credential loading.
        from quant.console.export import write_export

        written = write_export(out_dir=args.out)
        print(f"Wrote {len(written)} export files:")
        for path in written:
            print(f"  {path}")
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
