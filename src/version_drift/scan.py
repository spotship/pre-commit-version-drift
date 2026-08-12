"""Sweeping a repo for pins that disagree with the declared version."""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .model import Rule, Toolchain, parse_version

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
    return {forms[rule.form], *(forms[name] for name in rule.also_accepts)}


def is_acceptable(rule: Rule, found: str, forms: dict[str, str]) -> bool:
    if found in accepted_forms(rule, forms):
        return True
    # A floor below the pin is a support statement, not drift.
    return bool(rule.floor and parse_version(found) <= parse_version(forms[rule.form]))


def _first_bad(rule: Rule, line: str, forms: dict[str, str]) -> re.Match[str] | None:
    for match in rule.pattern.finditer(line):
        if not is_acceptable(rule, match.group(1), forms):
            return match
    return None


def scan(root: Path, toolchain: Toolchain, declared: str, fix: bool = False) -> Result:
    forms = toolchain.resolve(declared)
    ignores = load_ignores(root, toolchain.ignore_file)
    result = Result()

    for rel in tracked_files(root):
        # The source of truth cannot disagree with itself.
        if rel == toolchain.source_file:
            continue
        if ignores and matches_any(rel, ignores):
            continue
        applicable = [rule for rule in toolchain.rules if matches_any(rel, rule.globs)]
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
                if fix and not rule.floor:
                    for _ in range(MAX_FIX_PASSES):
                        match = _first_bad(rule, lines[index], forms)
                        if match is None:
                            break
                        start, end = match.span(1)
                        line = lines[index]
                        lines[index] = line[:start] + forms[rule.form] + line[end:]
                        changed = True
                    continue

                for match in rule.pattern.finditer(lines[index]):
                    found = match.group(1)
                    if is_acceptable(rule, found, forms):
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
                            expected=" or ".join(sorted(accepted_forms(rule, forms))),
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
