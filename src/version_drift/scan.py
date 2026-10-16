"""Sweeping a repo for pins that disagree with the declared version."""

from __future__ import annotations

import fnmatch
import json
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


def read_declared(root: Path, toolchain: Toolchain) -> str | None:
    """The declared version, or None when this toolchain does not apply here.

    None covers both "no source file" and, for a field-sourced toolchain, "the
    file is here but does not carry the field" -- every repo in a polyglot estate
    has a package.json for semantic-release, and only the ones using pnpm set
    `packageManager`. Treating the field's absence as an error would fail every
    Python repo on a toolchain that has nothing to do with them.
    """
    source = root / toolchain.source_file
    if not source.is_file():
        return None
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if toolchain.source_search:
        match = toolchain.source_search.search(text)
        return match.group(1) if match else None
    if not toolchain.source_field:
        return text.strip()
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return read_field(data, toolchain.source_field)


def load_ignores(root: Path, ignore_file: str) -> list[str]:
    path = root / ignore_file
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def read_field(data: object, dotted: str) -> str | None:
    """Look up a dotted path, treating only the first segment as a container.

    Returns the value only when it is a string: a version is always written as
    one, and `"engines": "22"` vs `"engines": {...}` should miss rather than
    stringify a dict into something a pattern might match.
    """
    if not isinstance(data, dict):
        return None
    head, _, tail = dotted.partition(".")
    if head not in data:
        return None
    value = data[head]
    if tail:
        if not isinstance(value, dict):
            return None
        value = value.get(tail)
    return value if isinstance(value, str) else None


def field_lineno(lines: list[str], dotted: str) -> int:
    """1-indexed line holding the field's own key, or 0 if it cannot be found.

    Approximate by design: a two-level path is located by its leaf, so a repo
    with several `"node"` keys can be pointed at the wrong one. The finding
    names the field path as well as the line, so an off-by-a-few line number
    misleads nobody -- and a structured read has no line of its own to report.
    """
    leaf = dotted.rpartition(".")[2]
    needle = re.compile(rf'"{re.escape(leaf)}"\s*:')
    for index, line in enumerate(lines):
        if needle.search(line):
            return index + 1
    return 0


def has_pins(root: Path, toolchain: Toolchain) -> bool:
    """Whether the repo holds anything this toolchain's rules would read.

    Asked only of a toolchain with no source of truth, to tell "this language is
    not used here" from "this language is used here and nothing is checking it".
    A Python repo should not be lectured about `.nvmrc`; a Python repo that has
    lost its `.python-version` should be told, loudly, before a `packageManager`
    field it keeps for semantic-release lets the run pass as healthy.

    Stops at the first match, so the common answer (no) costs one pass over the
    files whose names the rules already claim.

    Honours the ignore file, because a path somebody has already excluded is not
    evidence that the language is in use -- and documentation describing these
    rules is the first thing to trip them, this repo's own README included.
    """
    ignores = load_ignores(root, toolchain.ignore_file)
    for rel in tracked_files(root):
        if matches_any(rel, toolchain.skip_globs):
            continue
        if ignores and matches_any(rel, ignores):
            continue
        applicable = [rule for rule in toolchain.rules if matches_any(rel, rule.globs)]
        if not applicable:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        data = None
        for rule in applicable:
            if rule.field:
                if data is None:
                    try:
                        data = json.loads(text)
                    except ValueError:
                        break
                if read_field(data, rule.field) is not None:
                    return True
            elif rule.pattern.search(text):
                return True
    return False


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
    needs rather than correcting it. A rule may also opt out outright, where the
    source of truth is the side more likely to be the stale one."""
    return rule.fixable and not rule.floor and rule.form in forms


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


def _scan_fields(
    *,
    rel: str,
    text: str,
    lines: list[str],
    rules: list[Rule],
    toolchain: Toolchain,
    forms: dict[str, str],
    declared: str,
    result: Result,
) -> None:
    """Check the structured rules against one parsed file.

    Never fixes. A rewrite here would have to reserialise the JSON, reformatting
    a file nobody asked it to touch, and the field forms it reads are ranges
    ("^22.20.1", ">=22 <23") whose correct replacement is not a version but a
    judgement about how wide the range should be.

    There is no inline escape either: JSON has no comments, so a field that
    genuinely cannot track the repo version has to be excluded by putting the
    file in the ignore file.
    """
    try:
        data = json.loads(text)
    except ValueError:
        # A package.json that does not parse is somebody else's failure to
        # report; every other hook in the stack will say so first.
        return

    for rule in rules:
        # A field-sourced toolchain reads its own source file, so that its other
        # fields are checked -- but the field it took the version *from* is the
        # one thing in there that cannot disagree with itself.
        if rel == toolchain.source_file and rule.field == toolchain.source_field:
            continue
        value = read_field(data, rule.field)
        if value is None:
            continue
        match = rule.pattern.search(value)
        if match is None or is_acceptable(rule, match.group(1), toolchain, forms):
            continue
        result.findings.append(
            Finding(
                path=rel,
                lineno=field_lineno(lines, rule.field),
                rule=rule.name,
                found=match.group(1),
                expected=expected_text(rule, toolchain, forms),
                line=f'"{rule.field}": {value!r}',
                source=toolchain.source_file,
                declared=declared,
                floor=rule.floor,
            )
        )


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
        # The source of truth cannot disagree with itself -- but only when the
        # whole file *is* the version. pnpm's truth is one field of a
        # package.json whose other fields (`engines.pnpm`) can drift from it
        # freely, so a field-sourced toolchain reads its own source file and
        # excludes just that field, below.
        if rel == toolchain.source_file and not toolchain.source_field:
            continue
        if matches_any(rel, toolchain.skip_globs):
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

        structured = [rule for rule in applicable if rule.field]
        if structured:
            _scan_fields(
                rel=rel,
                text=text,
                lines=lines,
                rules=structured,
                toolchain=toolchain,
                forms=forms,
                declared=declared,
                result=result,
            )
        applicable = [rule for rule in applicable if not rule.field]

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
