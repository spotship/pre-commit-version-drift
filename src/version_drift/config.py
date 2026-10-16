"""Loading toolchain definitions from TOML.

Built-in toolchains ship as package data next to this module. A repo can add or
override toolchains in a ``.version-drift.toml`` at its root, which is how a
language this package has never heard of gets checked without a code change.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

from .model import DEFAULT_SOURCE_RE, ConfigError, Rule, Toolchain, validate

BUILTIN_DIR = Path(__file__).parent / "toolchains"
USER_CONFIG = ".version-drift.toml"

_DEFAULTS = {"components": 2}


def _expand_globs(raw: list[str], groups: dict[str, list[str]], rule_name: str) -> tuple[str, ...]:
    """``["@config", "*.py"]`` -> the config group's globs plus ``*.py``.

    Named groups exist because the same dozen config extensions are shared by
    most rules, and a drifting copy of that list is its own kind of bug.
    """
    out: list[str] = []
    for entry in raw:
        if not entry.startswith("@"):
            out.append(entry)
            continue
        key = entry[1:]
        if key not in groups:
            raise ConfigError(
                f"rule {rule_name!r} references glob group {entry!r}, "
                f"which is not defined (have: {sorted(groups)})"
            )
        out.extend(groups[key])
    return tuple(dict.fromkeys(out))  # de-duplicate, keep order


def toolchain_from_dict(name: str, data: dict[str, Any]) -> Toolchain:
    try:
        source_file = data["source_file"]
    except KeyError as exc:
        raise ConfigError(f"toolchain {name!r} has no source_file") from exc

    raw_source = data.get("source_pattern")
    if raw_source is None:
        source_pattern = DEFAULT_SOURCE_RE
    else:
        try:
            source_pattern = re.compile(raw_source)
        except re.error as exc:
            raise ConfigError(f"toolchain {name!r} has an invalid source_pattern: {exc}") from exc
        if source_pattern.groups != 1:
            raise ConfigError(
                f"toolchain {name!r} source_pattern must capture exactly one group "
                f"(the dotted version); it captures {source_pattern.groups}"
            )

    groups = {key: list(value) for key, value in data.get("globs", {}).items()}
    rules: list[Rule] = []
    for raw in data.get("rules", []):
        rule_name = raw.get("name", "<unnamed>")
        try:
            pattern = re.compile(raw["pattern"])
        except KeyError as exc:
            raise ConfigError(f"rule {rule_name!r} has no pattern") from exc
        except re.error as exc:
            raise ConfigError(f"rule {rule_name!r} has an invalid pattern: {exc}") from exc
        rules.append(
            Rule(
                name=rule_name,
                pattern=pattern,
                form=raw.get("form", ""),
                globs=_expand_globs(raw.get("globs", []), groups, rule_name),
                field=raw.get("field", ""),
                also_accepts=tuple(raw.get("also_accepts", ())),
                floor=bool(raw.get("floor", False)),
            )
        )

    return validate(
        Toolchain(
            name=name,
            source_file=source_file,
            forms=dict(data.get("forms", {})),
            rules=tuple(rules),
            components=int(data.get("components", _DEFAULTS["components"])),
            inline_escape=data.get("inline_escape", f"{name}-version-ok"),
            ignore_file=data.get("ignore_file", f".{name}-version-ignore"),
            source_pattern=source_pattern,
            source_field=data.get("source_field", ""),
            skip_globs=tuple(data.get("skip_globs", ())),
        )
    )


def load_builtin() -> dict[str, Toolchain]:
    out: dict[str, Toolchain] = {}
    for path in sorted(BUILTIN_DIR.glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        name = data.get("name", path.stem)
        out[name] = toolchain_from_dict(name, data)
    return out


def load(root: Path) -> dict[str, Toolchain]:
    """Built-in toolchains, then the repo's own overrides applied on top."""
    toolchains = load_builtin()

    user_path = root / USER_CONFIG
    if not user_path.is_file():
        return toolchains

    data = tomllib.loads(user_path.read_text(encoding="utf-8"))
    for name, raw in data.get("toolchain", {}).items():
        if raw.get("enabled") is False:
            toolchains.pop(name, None)
            continue
        # A stanza carrying only `enabled = true` re-states a built-in rather
        # than replacing it, so there is nothing to rebuild.
        if set(raw) <= {"enabled"}:
            if name not in toolchains:
                raise ConfigError(
                    f"{USER_CONFIG} enables unknown toolchain {name!r}; "
                    f"define it there, or use one of {sorted(toolchains)}"
                )
            continue
        toolchains[name] = toolchain_from_dict(name, raw)

    return toolchains
