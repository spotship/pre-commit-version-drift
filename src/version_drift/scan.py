"""Sweeping a repo for pins that disagree with the declared version."""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .model import Rule, Toolchain, declared_version, parse_version

# A rewrite that re-matches its own output would spin forever. Nothing legitimate
# needs more than a handful of passes over one line; this only bounds a bug.
MAX_FIX_PASSES = 100


@dataclass(frozen=True)
class Finding:
    path: str
    lineno: int
    rule: str
    found: str
    expected: str
    line: str
    source: str
    declared: str
    floor: bool = False

    def render(self) -> str:
        if self.floor:
            head = (
                f"{self.path}:{self.lineno}: [{self.rule}] floor {self.found!r} is "
                f"above {self.source} ({self.declared}), so it excludes the "
                f"version this repo runs on"
            )
        else:
            head = (
                f"{self.path}:{self.lineno}: [{self.rule}] found {self.found!r}, "
                f"expected {self.expected} (from {self.source} = {self.declared})"
            )
        return f"{head}\n    {self.line.strip()}"


@dataclass
class Result:
    findings: list[Finding] = field(default_factory=list)
    fixed: list[str] = field(default_factory=list)
    # Rules that could not run because the source file is less precise than they
    # are. Not a failure -- but reporting nothing while silently checking less
    # than advertised is the same silent pass this tool exists to end.
    unchecked: list[str] = field(default_factory=list)
    declared_components: int = 0

    def ok(self) -> bool:
        return not self.findings and not self.fixed


def repo_root(start: Path | None = None) -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(out.stdout.strip())


def tracked_files(root: Path) -> list[str]:
    """Only git-tracked files. A build artifact or a vendored tree is not ours."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True, check=True
    )
    return [path for path in out.stdout.split("\0") if path]


def load_ignores(root: Path, ignore_file: str) -> list[str]:
    path = root / ignore_file
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def matches_any(path: str, globs: tuple[str, ...] | list[str]) -> bool:
    """fnmatch against the full path *and* the basename.

    fnmatch, not gitignore syntax: ``*`` crosses ``/``, so ``vendor/*`` matches
    ``vendor/a/b.yml``. Testing the basename too is what lets ``*.yml`` match a
    file at any depth.
    """
    name = Path(path).name
    return any(fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(name, glob) for glob in globs)


def accepted_forms(rule: Rule, forms: dict[str, str]) -> set[str]:
    """The values this rule will accept, limited to forms the source file supports.

    A short source file leaves some forms unbuildable, so this can come back
    empty -- see `can_run`, which is what keeps that case from being read as
    "accepts nothing, so everything is drift".
    """
    names = (rule.form, *rule.also_accepts)
    return {forms[name] for name in names if name in forms}


def can_check(rule: Rule, toolchain: Toolchain, forms: dict[str, str]) -> bool:
    """Whether this rule has anything to compare a captured value against.

    A floor is *ordered* against the pin rather than matched, so it needs its own
    form exactly; truncating one side of a comparison that means "at least this"
    would change what is being asserted. Everything else can fall back to a
    shared prefix.
    """
    if rule.floor:
        return rule.form in forms
    if accepted_forms(rule, forms):
        return True
    return toolchain.fallback_form(rule.form, forms) is not None


def can_fix(rule: Rule, forms: dict[str, str]) -> bool:
    """`--fix` writes `rule.form` into the file, so a prefix will not do: that
    would rewrite `python:3.9.1` to `python:3.12`, deleting a component the tag
    needs rather than correcting it."""
    return not rule.floor and rule.form in forms


def expected_text(rule: Rule, toolchain: Toolchain, forms: dict[str, str]) -> str:
    """What to tell the reader this rule wanted.

    When the rule is running on a shared prefix, its own form has no value, so
    naming the prefix is the only description that matches what was compared --
    and it says, without a second line, that the patch was not part of the test.
    """
    accepted = accepted_forms(rule, forms)
    if accepted:
        return " or ".join(sorted(accepted))
    fallback = toolchain.fallback_form(rule.form, forms)
    return fallback[0] if fallback else ""


def is_acceptable(rule: Rule, found: str, toolchain: Toolchain, forms: dict[str, str]) -> bool:
    if found in accepted_forms(rule, forms):
        return True
    # A floor below the pin is a support statement, not drift.
    if rule.floor:
        return parse_version(found) <= parse_version(forms[rule.form])
    fallback = toolchain.fallback_form(rule.form, forms)
    if fallback is None:
        return False
    value, components = fallback
    return ".".join(found.split(".")[:components]) == value


def _first_bad(
    rule: Rule, line: str, toolchain: Toolchain, forms: dict[str, str]
) -> re.Match[str] | None:
    for match in rule.pattern.finditer(line):
        if not is_acceptable(rule, match.group(1), toolchain, forms):
            return match
    return None


def scan(root: Path, toolchain: Toolchain, declared: str, fix: bool = False) -> Result:
    forms = toolchain.resolve(declared)
    ignores = load_ignores(root, toolchain.ignore_file)

    # Partitioned once, not per file: whether a rule can run at all is a property
    # of the source file's precision, which does not change mid-scan.
    runnable = [rule for rule in toolchain.rules if can_check(rule, toolchain, forms)]
    result = Result(
        unchecked=[rule.name for rule in toolchain.rules if not can_check(rule, toolchain, forms)],
        declared_components=len(
            declared_version(declared, toolchain.source_file, toolchain.source_pattern)
        ),
    )

    for rel in tracked_files(root):
        # The source of truth cannot disagree with itself.
        if rel == toolchain.source_file:
            continue
        if ignores and matches_any(rel, ignores):
            continue
        applicable = [rule for rule in runnable if matches_any(rel, rule.globs)]
        if not applicable:
            continue

        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        lines = text.splitlines(keepends=True)
        changed = False

        for index in range(len(lines)):
            if toolchain.inline_escape in lines[index]:
                continue
            # One pin can satisfy two rules: `build-python3.12` matches both the
            # SAM-image rule and the interpreter rule at the same offsets. Report
            # it once, under the first rule that claims it, so the count matches
            # the number of places somebody has to edit.
            reported: set[tuple[int, int]] = set()

            for rule in applicable:
                if fix and can_fix(rule, forms):
                    for _ in range(MAX_FIX_PASSES):
                        match = _first_bad(rule, lines[index], toolchain, forms)
                        if match is None:
                            break
                        start, end = match.span(1)
                        line = lines[index]
                        lines[index] = line[:start] + forms[rule.form] + line[end:]
                        changed = True
                    continue

                # Reached under --fix too, for a rule --fix cannot touch: a
                # floor, or one whose form the source file cannot fill. Reporting
                # is the only honest option left for those.
                for match in rule.pattern.finditer(lines[index]):
                    found = match.group(1)
                    if is_acceptable(rule, found, toolchain, forms):
                        continue
                    if match.span(1) in reported:
                        continue
                    reported.add(match.span(1))
                    result.findings.append(
                        Finding(
                            path=rel,
                            lineno=index + 1,
                            rule=rule.name,
                            found=found,
                            expected=expected_text(rule, toolchain, forms),
                            line=lines[index],
                            source=toolchain.source_file,
                            declared=declared,
                            floor=rule.floor,
                        )
                    )

        if changed:
            path.write_text("".join(lines), encoding="utf-8")
            result.fixed.append(rel)

    return result
