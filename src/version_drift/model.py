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
from collections.abc import Mapping
from dataclasses import dataclass

# Positional names for the components of a dotted version, in order. A toolchain
# whose source file holds "3.12.13" can write forms in terms of {major}, {minor}
# and {patch}; one holding "22" gets {major} only.
COMPONENT_NAMES = ("major", "minor", "patch")

_VERSION_RE = re.compile(r"^v?(\d+(?:\.\d+)*)$")


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


@dataclass(frozen=True)
class Rule:
    """One class of version pin, and the form it has to be written in."""

    name: str
    pattern: re.Pattern[str]
    form: str
    globs: tuple[str, ...]
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
    components: int
    inline_escape: str
    ignore_file: str

    def resolve(self, declared: str) -> dict[str, str]:
        """Expand every form template against a declared version.

        ``{"patch": "{major}.{minor}.{patch}"}`` and ``"3.12.13"`` gives
        ``{"patch": "3.12.13"}``.
        """
        values = parse_version(declared)
        if len(values) < self.components:
            raise ConfigError(
                f"{self.source_file} must hold at least {self.components} "
                f"version components for the {self.name} toolchain, got {declared!r}"
            )
        parts = dict(zip(COMPONENT_NAMES, values, strict=False))
        resolved: dict[str, str] = {}
        for name, template in self.forms.items():
            try:
                resolved[name] = template.format(**parts)
            except (KeyError, IndexError) as exc:
                raise ConfigError(
                    f"form {name!r} of toolchain {self.name!r} references {exc} "
                    f"but {self.source_file} only supplies {len(values)} component(s)"
                ) from exc
        return resolved


def validate(toolchain: Toolchain) -> Toolchain:
    """Reject a toolchain that would fail confusingly mid-scan."""
    if not toolchain.forms:
        raise ConfigError(f"toolchain {toolchain.name!r} defines no forms")

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
        # A floor is compared by ordering, so its form has to be a dotted version.
        # Ordering "312" against "39" is arithmetic on a number that means nothing.
        if rule.floor and "." not in toolchain.forms[rule.form]:
            raise ConfigError(
                f"floor rule {rule.name!r} uses form {rule.form!r}, which is not "
                f"dotted; a floor has to be orderable against the pin"
            )
    return toolchain
