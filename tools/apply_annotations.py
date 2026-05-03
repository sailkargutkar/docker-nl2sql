"""
Review and merge proposed schema annotations into the live DSL.

Usage:
  python tools/apply_annotations.py --review
      Show a colored diff of <schema>.proposed.yml against <schema>.yml.

  python tools/apply_annotations.py --apply
      Back up the live schema and replace it with the proposed version.

  python tools/apply_annotations.py --discard
      Delete the .proposed.yml without applying.
"""

from __future__ import annotations

import argparse
import difflib
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools._common import ROOT  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--schema", default="schema/tmt_schema.yml")
    p.add_argument("--proposed", default=None,
                   help="Default: <schema>.proposed.yml")
    p.add_argument("--review", action="store_true", help="Show diff and exit.")
    p.add_argument("--apply", action="store_true",
                   help="Back up live and replace with proposed.")
    p.add_argument("--discard", action="store_true",
                   help="Delete the proposed file.")
    p.add_argument("--yes", action="store_true",
                   help="Skip the confirmation prompt for --apply.")
    return p.parse_args(argv)


def _resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    schema = Path(args.schema)
    if not schema.is_absolute():
        schema = ROOT / schema
    proposed = Path(args.proposed) if args.proposed else schema.with_suffix(".proposed.yml")
    if not proposed.is_absolute():
        proposed = ROOT / proposed
    return schema, proposed


def _show_diff(schema: Path, proposed: Path) -> None:
    a = schema.read_text().splitlines(keepends=True) if schema.is_file() else []
    b = proposed.read_text().splitlines(keepends=True) if proposed.is_file() else []
    diff = difflib.unified_diff(a, b, fromfile=str(schema), tofile=str(proposed))
    n_changes = 0
    for line in diff:
        sys.stdout.write(line)
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            n_changes += 1
    if n_changes == 0:
        sys.stderr.write("(no differences)\n")
    else:
        sys.stderr.write(f"\n({n_changes} changed lines)\n")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not (args.review or args.apply or args.discard):
        sys.stderr.write("ERROR: pick one of --review, --apply, --discard\n")
        return 2

    schema, proposed = _resolve_paths(args)
    if not proposed.is_file():
        sys.stderr.write(f"ERROR: no proposed file at {proposed}\n")
        sys.stderr.write("  Run `python tools/annotate_schema.py ...` first.\n")
        return 2

    if args.review:
        _show_diff(schema, proposed)
        return 0

    if args.discard:
        proposed.unlink()
        sys.stderr.write(f"Deleted: {proposed}\n")
        return 0

    # --apply
    if not args.yes:
        sys.stderr.write(f"This will replace {schema} with {proposed}.\n")
        sys.stderr.write("Diff preview:\n")
        _show_diff(schema, proposed)
        sys.stderr.write("\nProceed? [y/N] ")
        sys.stderr.flush()
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = ""
        if answer != "y":
            sys.stderr.write("Aborted.\n")
            return 1

    backup = schema.with_suffix(f".yml.bak.{int(time.time())}")
    if schema.is_file():
        shutil.copy2(schema, backup)
    shutil.move(str(proposed), str(schema))
    sys.stderr.write(f"Applied. Backup: {backup}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
