"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import config
from .model import COMPONENT_NAMES, ConfigError, declared_version
from .scan import Result, has_pins, read_declared, repo_root, scan


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
    absent: list[str] = []
    for name, toolchain in sorted(selected.items()):
        found = args.declared.strip() if args.declared else read_declared(root, toolchain)
        if args.declared:
            # Parse it here, under its own name. Left to the scan, a bad
            # --declared is reported as something the source file said -- and for
            # a field-sourced toolchain it is easy to pass "10.1.0" where the
            # field's own form ("pnpm@10.1.0") is what stands in.
            try:
                declared_version(found, "--declared", toolchain.source_pattern)
            except ConfigError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
        if found is None:
            # A Python repo has no .nvmrc, and has a package.json for
            # semantic-release that carries no `packageManager`. Either absence
            # means "not this toolchain", never "nothing to check" -- that
            # verdict is made below, once every toolchain has had its say.
            if has_pins(root, toolchain):
                # Three different absences, and saying the wrong one sends the
                # reader to the wrong place: "package.json has no packageManager"
                # is a lie about a repo with no package.json, and
                # ".pre-commit-config.yaml absent" is a lie about a config that
                # is there but lists no ruff hook.
                if not (root / toolchain.source_file).is_file():
                    missing = "absent"
                elif toolchain.source_field:
                    missing = f"has no {toolchain.source_field}"
                else:
                    missing = f"names no {name} version"
                absent.append(f"{name} ({toolchain.source_file} {missing})")
            continue
        declared = found
        try:
            results[name] = scan(root, toolchain, declared, fix=args.fix)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if not results:
        # De-duplicated: ruff and djLint both read .pre-commit-config.yaml, and
        # naming it twice reads as a typo rather than as two toolchains.
        expected = ", ".join(
            sorted(
                {
                    f"{t.source_file} ({t.source_field})" if t.source_field else t.source_file
                    for t in selected.values()
                }
            )
        )
        print(
            f"error: no source-of-truth file found in {root}. Create one of: "
            f"{expected} -- it is what every other version pin is checked against.",
            file=sys.stderr,
        )
        return 1

    # One toolchain resolving says nothing about the others. A Python repo
    # carrying a `packageManager` for semantic-release now satisfies the "some
    # source exists" test above on pnpm alone, which would turn the loud "no
    # source of truth" error into a pass that reads as a clean bill of health
    # while no Python pin in the repo has been looked at.
    if absent:
        print(
            f"Not checked: {', '.join(absent)}. This repo has pins those rules "
            f"would read, and nothing is checking them."
        )

    multiple = len(results) > 1
    for name, result in results.items():
        label = f"[{name}] " if multiple else ""
        toolchain = selected[name]

        # Say so when the source file is less precise than the rule set. The scan
        # still passes -- a file saying "3.12" cannot adjudicate a patch, and
        # inventing one would manufacture drift -- but a consumer who thinks
        # every pin is covered should not have to read the source to find out
        # otherwise.
        if result.declared_components < toolchain.components:
            print(
                f"{label}{toolchain.source_file} supplies "
                f"{result.declared_components} of {toolchain.components} components, so pins "
                f"are compared on their first {result.declared_components}: a wrong "
                f"{COMPONENT_NAMES[result.declared_components]} cannot be detected from it."
            )
        if result.unchecked:
            print(
                f"{label}{len(result.unchecked)} rule(s) have no form this "
                f"{toolchain.source_file} can fill, and did not run: "
                f"{', '.join(sorted(result.unchecked))}"
            )

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

            # Named per toolchain, not once at the end. Each has its own escape
            # and its own ignore file, and pointing a pnpm finding at
            # `node-version-ok` sends the reader to a hatch that will not open.
            print(
                f"\n{label}Fix the pins, or add an inline `{toolchain.inline_escape}` "
                f"comment / a glob in {toolchain.ignore_file} if a pin genuinely "
                f"cannot track the repo version.",
                file=sys.stderr,
            )

    return 1 if any(not result.ok() for result in results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
