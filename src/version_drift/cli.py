"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config
from .model import ConfigError
from .scan import Result, repo_root, scan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="version-drift",
        description="Fail if any pinned toolchain version disagrees with its source-of-truth file.",
    )
    parser.add_argument("--root", default=None, help="repo to check (default: enclosing git repo)")
    parser.add_argument(
        "--toolchain",
        action="append",
        dest="toolchains",
        metavar="NAME",
        help="restrict to one toolchain; repeatable",
    )
    parser.add_argument(
        "--declared",
        default=None,
        help="override the source-of-truth file, for a dry run before it exists "
        "(requires a single --toolchain)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="rewrite drifted versions in place. Only the version itself is "
        "rewritten -- a co-located tag (e.g. the '-alpine3.20' in "
        "'python:3.12.10-alpine3.20') is left alone and may still need a "
        "manual bump to name something that exists.",
    )
    parser.add_argument(
        "--list-toolchains", action="store_true", help="print known toolchains and exit"
    )
    parser.add_argument(
        "filenames",
        nargs="*",
        help="ignored; the whole repo is always swept, so that editing the "
        "source-of-truth file re-validates every pin",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else repo_root()

    try:
        known = config.load(root)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list_toolchains:
        for name, toolchain in sorted(known.items()):
            print(f"{name}\t{toolchain.source_file}\t{len(toolchain.rules)} rules")
        return 0

    if args.toolchains:
        unknown = [name for name in args.toolchains if name not in known]
        if unknown:
            print(
                f"error: unknown toolchain(s) {', '.join(unknown)}; "
                f"known: {', '.join(sorted(known))}",
                file=sys.stderr,
            )
            return 2
        selected = {name: known[name] for name in args.toolchains}
    else:
        selected = known

    if args.declared and len(selected) != 1:
        print(
            "error: --declared applies to one toolchain; pass a single --toolchain",
            file=sys.stderr,
        )
        return 2

    results: dict[str, Result] = {}
    for name, toolchain in sorted(selected.items()):
        source = root / toolchain.source_file
        if args.declared:
            declared = args.declared.strip()
        elif source.is_file():
            declared = source.read_text(encoding="utf-8").strip()
        else:
            # A Python repo has no .nvmrc. Absence means "not this toolchain",
            # never "nothing to check" -- that verdict is made below, once every
            # toolchain has had its say.
            continue
        try:
            results[name] = scan(root, toolchain, declared, fix=args.fix)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if not results:
        expected = ", ".join(sorted(t.source_file for t in selected.values()))
        print(
            f"error: no source-of-truth file found in {root}. Create one of: "
            f"{expected} -- it is what every other version pin is checked against.",
            file=sys.stderr,
        )
        return 1

    multiple = len(results) > 1
    for name, result in results.items():
        label = f"[{name}] " if multiple else ""

        if result.fixed:
            print(f"{label}Rewrote version pins in {len(result.fixed)} file(s):")
            for rel in result.fixed:
                print(f"  {rel}")
            print(
                "\nReview the diff -- a co-located tag (e.g. '-alpine3.20') is not "
                "rewritten and may now name something that does not exist."
            )

        # Not elif: --fix leaves floors alone, so one run can both rewrite pins
        # and still have something to report. Hiding one behind the other hides
        # the half that needs a human.
        if result.findings:
            print(
                f"\n{label}Version drift: {len(result.findings)} pin(s) disagree "
                f"with the declared version\n",
                file=sys.stderr,
            )
            for finding in result.findings:
                print(finding.render(), file=sys.stderr)

    if any(not result.ok() for result in results.values()):
        escapes = sorted({t.inline_escape for t in selected.values()})
        ignores = sorted({t.ignore_file for t in selected.values()})
        print(
            f"\nFix the pins, or add an inline `{escapes[0]}` comment / a glob in "
            f"{ignores[0]} if a pin genuinely cannot track the repo version.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
