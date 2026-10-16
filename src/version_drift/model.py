"""Core data model: versions, forms, rules, toolchains.

A *toolchain* is one language's answer to "what version does this repo run on".
It names a source-of-truth file (``.python-version``, ``.nvmrc``, ...), the
**forms** that version is legitimately written in, and the **rules** saying where
each form is allowed to appear.

Nothing in here is Python-specific. The Python rules are data; see
``toolchains/python.toml``.
"""

from __future__ import annotations

import re
import string
from collections.abc import Mapping
from dataclasses import dataclass

# Positional names for the components of a dotted version, in order. A toolchain
# whose source file holds "3.12.13" can write forms in terms of {major}, {minor}
# and {patch}; one holding "22" gets {major} only.
COMPONENT_NAMES = ("major", "minor", "patch")

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")

# The default reader for a source-of-truth file: a bare dotted version, nothing
# else. A toolchain whose file is written by tools that put more in it supplies
# its own `source_pattern` -- see toolchains/python.toml, where pyenv and uv
# between them write `3.13t`, `cpython@3.12` and `pypy3.10-7.3.12`.
DEFAULT_SOURCE_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")


class ConfigError(ValueError):
    """A toolchain definition is unusable. Raised at load time, not mid-scan."""


def parse_version(text: str) -> tuple[int, ...]:
    """``"v3.12.13"`` -> ``(3, 12, 13)``.

    Tuples of ints, not strings, because floors are *ordered* against the pin:
    string comparison would put "3.9" above "3.12".
    """
    match = _VERSION_RE.match(text.strip())
    if not match:
        raise ConfigError(f"{text!r} is not a dotted numeric version")
    return tuple(int(part) for part in match.group(1).split("."))


def declared_version(
    text: str, source_file: str, source_pattern: re.Pattern[str]
) -> tuple[int, ...]:
    """Read a source-of-truth file's contents into version components.

    This is deliberately not ``parse_version``. That one compares values captured
    out of a repo's own files, which are always bare dotted numbers because a
    rule's pattern says so. *This* one reads a file written by somebody else's
    tool, and those tools disagree about the format.

    Only the first non-blank line is read. ``pyenv local 3.12 3.11`` writes one
    version per line and resolves its shims to the first, so the later lines are
    alternates on PATH rather than a second opinion about what the repo runs on.
    """
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        match = source_pattern.match(candidate)
        if not match:
            raise ConfigError(
                f"{source_file} says {candidate!r}, which names no version this "
                f"toolchain can read. Pins are checked against it, so there is "
                f"nothing to check until it holds one"
            )
        return tuple(int(part) for part in match.group(1).split("."))
    raise ConfigError(f"{source_file} is empty; it is what every pin is checked against")


@dataclass(frozen=True)
class Rule:
    """One class of version pin, and the form it has to be written in."""

    name: str
    pattern: re.Pattern[str]
    form: str
    globs: tuple[str, ...]
    # A dotted path into a structured file, at most two levels
    # ("engines.node", "devDependencies.@types/node", "packageManager").
    #
    # Set, the rule reads that field and applies `pattern` to its string value
    # instead of to each line. Same contract either way -- group 1 is the version
    # -- only the haystack changes. Parsing rather than matching is what keeps a
    # dependency *named* "node" out of an engines check: frontend's root
    # package.json has a top-level "pnpm" config object that any regex for
    # `"pnpm":` would hit.
    field: str = ""
    # Some pins are legitimate at more than one precision -- a workflow may name
    # either "3.12" or "3.12.13" and both are correct.
    also_accepts: tuple[str, ...] = ()
    # A floor declares the *oldest* version supported, not the one this repo runs
    # on, so anything at or below the pin is correct. Only a floor above the pin
    # is wrong: it excludes the interpreter in use. Floors are never rewritten --
    # raising one drops support that still works.
    floor: bool = False


@dataclass(frozen=True)
class Toolchain:
    name: str
    source_file: str
    forms: Mapping[str, str]
    rules: tuple[Rule, ...]
    # How many components the source file must supply for *every* rule to run.
    # Fewer is allowed: the forms that need the missing components cannot be
    # built, so the rules written in them are skipped and the scan reports the
    # reduced coverage. This is not laxity -- a repo whose file says "3.12"
    # genuinely cannot say whether `python:3.12.9` is the right patch, and
    # guessing one would invent drift. Heroku recommends the two-component form
    # outright, so demanding three locks the tool out of those repos entirely.
    components: int
    inline_escape: str
    ignore_file: str
    source_pattern: re.Pattern[str] = DEFAULT_SOURCE_RE
    # A dotted path to the version *inside* source_file, for a toolchain whose
    # truth is a field rather than a whole file. pnpm's is
    # `packageManager: "pnpm@9.15.9"` in package.json, and no .pnpm-version
    # convention exists to use instead. source_pattern then extracts the version
    # from that field's value, the same two steps that read `pypy3.10-7.3.12`
    # out of a .python-version.
    source_field: str = ""
    # Files never swept, whatever the rules' globs say. Node's rules cover
    # *.yml, which includes pnpm-lock.yaml, where third-party package names
    # ("parse-node-version: 1.0.1") read as pins. It cannot be a global: the
    # Python toolchain deliberately sweeps uv.lock, because a stale floor there
    # means somebody edited pyproject.toml without re-locking.
    skip_globs: tuple[str, ...] = ()

    def resolve(self, declared: str) -> dict[str, str]:
        """Expand each form template that the declared version can satisfy.

        ``{"patch": "{major}.{minor}.{patch}"}`` and ``"3.12.13"`` gives
        ``{"patch": "3.12.13"}``. The same form against ``"3.12"`` gives nothing
        -- the patch is not knowable, so no rule may claim to check it.
        """
        values = declared_version(declared, self.source_file, self.source_pattern)
        parts = dict(zip(COMPONENT_NAMES, values, strict=False))
        resolved: dict[str, str] = {}
        for name, template in self.forms.items():
            try:
                resolved[name] = template.format(**parts)
            except KeyError:
                # Only an absent *component* reaches here; validate() has
                # already rejected a form naming something that is not one.
                continue
        if not resolved:
            raise ConfigError(
                f"{self.source_file} supplies {len(values)} component(s) "
                f"({'.'.join(str(v) for v in values)}), which is not enough for any "
                f"form of the {self.name} toolchain: {sorted(self.forms)}"
            )
        return resolved

    def fallback_form(self, form: str, resolved: Mapping[str, str]) -> tuple[str, int] | None:
        """The coarsest-but-longest form to check `form` against when it is absent.

        Standing a rule down whenever its own form is unbuildable leaves a hole
        big enough to swallow the thing this tool is for: against a source file
        saying "3.12", the patch rule cannot run, and the minor rule's pattern
        refuses a three-component tag, so ``python:3.9.1`` would pass unread.

        A dotted form whose template is a prefix of the absent one shares its
        leading components, so the captured value can be truncated to that many
        and compared. ``{major}.{minor}`` is a prefix of
        ``{major}.{minor}.{patch}``: "3.9.1" truncates to "3.9", which is still
        wrong, and still reported. Returns the value to compare against and how
        many components to truncate to.
        """
        target = self.forms.get(form)
        if target is None or form in resolved:
            return None
        best: tuple[str, int] | None = None
        for name, template in self.forms.items():
            if name not in resolved or not target.startswith(f"{template}."):
                continue
            components = template.count("{")
            if best is None or components > best[1]:
                best = (resolved[name], components)
        return best


def validate(toolchain: Toolchain) -> Toolchain:
    """Reject a toolchain that would fail confusingly mid-scan."""
    if not toolchain.forms:
        raise ConfigError(f"toolchain {toolchain.name!r} defines no forms")

    # resolve() drops a form whose components the source file does not supply, so
    # a typo like "{pacth}" would otherwise vanish into that same skip and take
    # its rules quietly out of the scan. Catch it here, where it is a typo with a
    # name, rather than there, where it looks like a short version file.
    for name, template in toolchain.forms.items():
        for _, field_name, _, _ in string.Formatter().parse(template):
            if field_name is not None and field_name not in COMPONENT_NAMES:
                raise ConfigError(
                    f"form {name!r} of toolchain {toolchain.name!r} references "
                    f"{field_name!r}, which is not a version component; "
                    f"expected one of {list(COMPONENT_NAMES)}"
                )

    for rule in toolchain.rules:
        for form in (rule.form, *rule.also_accepts):
            if form not in toolchain.forms:
                raise ConfigError(
                    f"rule {rule.name!r} references unknown form {form!r}; "
                    f"toolchain {toolchain.name!r} defines {sorted(toolchain.forms)}"
                )
        if rule.pattern.groups != 1:
            raise ConfigError(
                f"rule {rule.name!r} must capture exactly one group (the version); "
                f"its pattern captures {rule.pattern.groups}"
            )
        if not rule.globs:
            raise ConfigError(f"rule {rule.name!r} matches no files")
        # Two levels is what package.json needs ("engines.node") and all a flat
        # lookup can do. A deeper path would read as working and always miss.
        if rule.field.count(".") > 1:
            raise ConfigError(
                f"rule {rule.name!r} has field {rule.field!r}; a field path is at "
                f'most two levels deep ("engines.node"), because the lookup '
                f"treats only the first segment as a container"
            )
        # A floor is compared by ordering, so its form has to be a dotted version.
        # Ordering "312" against "39" is arithmetic on a number that means nothing.
        if rule.floor and "." not in toolchain.forms[rule.form]:
            raise ConfigError(
                f"floor rule {rule.name!r} uses form {rule.form!r}, which is not "
                f"dotted; a floor has to be orderable against the pin"
            )
    return toolchain
