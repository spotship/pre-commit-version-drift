"""The toolchain-agnostic engine: versions, forms, config loading, CLI contract.

Nothing here is about Python the language -- the Node toolchain defined inline
below is the test that "generic" is a real claim and not a comment.
"""

from __future__ import annotations

import pytest

from version_drift.config import toolchain_from_dict
from version_drift.model import ConfigError, Toolchain, parse_version, validate

# A whole toolchain this package has never heard of, defined in the repo being
# checked. If this works, adding Go or Ruby is a TOML file, not a release.
NODE_TOOLCHAIN = """
[toolchain.node]
source_file = ".nvmrc"
components = 3
inline_escape = "node-version-ok"
ignore_file = ".node-version-ignore"

[toolchain.node.forms]
full = "{major}.{minor}.{patch}"
major = "{major}"
prefixed = "v{major}.{minor}.{patch}"

[toolchain.node.globs]
yaml = ["*.yml", "*.yaml"]

[[toolchain.node.rules]]
name = "setup-node"
pattern = '''node-version:\\s*['"]?(\\d+\\.\\d+\\.\\d+)'''
form = "full"
globs = ["@yaml"]

[[toolchain.node.rules]]
name = "docker-image"
pattern = '(?:^|[/\\s])node:(\\d+)(?![\\d.])'
form = "major"
globs = ["*Dockerfile"]
"""


# --------------------------------------------------------------------------
# Version parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("3.12.13", (3, 12, 13)), ("v22.22.0", (22, 22, 0)), ("22", (22,)), (" 1.2.3 ", (1, 2, 3))],
)
def test_parse_version(text, expected):
    assert parse_version(text) == expected


@pytest.mark.parametrize("text", ["", "3.x", "latest", "3.12.13-rc1"])
def test_parse_version_rejects_non_numeric(text):
    with pytest.raises(ConfigError):
        parse_version(text)


def test_versions_order_numerically_not_lexically(make_repo, run_cli):
    """The reason versions are tuples of ints: "3.9" is *below* "3.12", which
    string comparison gets backwards -- and would invert every floor."""
    repo = make_repo({"pyproject.toml": '[project]\nrequires-python = ">=3.9"\n'})
    assert run_cli(repo).code == 0


# --------------------------------------------------------------------------
# Form resolution
# --------------------------------------------------------------------------


def _toolchain(**overrides):
    base = {
        "source_file": ".x-version",
        "components": 2,
        "forms": {"minor": "{major}.{minor}"},
        "rules": [{"name": "r", "pattern": r"x(\d+\.\d+)", "form": "minor", "globs": ["*.txt"]}],
    }
    base.update(overrides)
    return toolchain_from_dict("x", base)


def test_forms_expand_from_the_declared_version():
    toolchain = _toolchain(
        components=3,
        forms={
            "patch": "{major}.{minor}.{patch}",
            "minor": "{major}.{minor}",
            "nodot": "{major}{minor}",
        },
    )
    assert toolchain.resolve("3.12.13") == {"patch": "3.12.13", "minor": "3.12", "nodot": "312"}


def test_a_source_file_with_too_few_components_is_rejected():
    with pytest.raises(ConfigError, match="at least 3 version components"):
        _toolchain(components=3).resolve("3.12")


def test_a_form_referencing_a_missing_component_is_rejected():
    toolchain = _toolchain(
        components=1,
        forms={"patch": "{major}.{minor}.{patch}"},
        rules=[{"name": "r", "pattern": r"x(\d+\.\d+)", "form": "patch", "globs": ["*.txt"]}],
    )
    with pytest.raises(ConfigError, match="only supplies 1 component"):
        toolchain.resolve("3")


# --------------------------------------------------------------------------
# Config validation. Every one of these fails at load time with a message
# naming the rule, rather than somewhere in the middle of a scan.
# --------------------------------------------------------------------------


def test_a_rule_naming_an_unknown_form_is_rejected():
    with pytest.raises(ConfigError, match="unknown form 'patch'"):
        _toolchain(rules=[{"name": "r", "pattern": r"(\d+)", "form": "patch", "globs": ["*.txt"]}])


def test_a_pattern_without_exactly_one_group_is_rejected():
    with pytest.raises(ConfigError, match="exactly one group"):
        _toolchain(
            rules=[{"name": "r", "pattern": r"(\d+)\.(\d+)", "form": "minor", "globs": ["*.txt"]}]
        )


def test_a_rule_matching_no_files_is_rejected():
    with pytest.raises(ConfigError, match="matches no files"):
        _toolchain(rules=[{"name": "r", "pattern": r"(\d+)", "form": "minor", "globs": []}])


def test_a_floor_on_an_undotted_form_is_rejected():
    """Ordering "312" against "39" is arithmetic on a number that means nothing."""
    with pytest.raises(ConfigError, match="not dotted"):
        _toolchain(
            forms={"nodot": "{major}{minor}"},
            rules=[
                {
                    "name": "r",
                    "pattern": r"(\d+)",
                    "form": "nodot",
                    "globs": ["*.txt"],
                    "floor": True,
                }
            ],
        )


def test_an_invalid_pattern_is_rejected():
    with pytest.raises(ConfigError, match="invalid pattern"):
        _toolchain(
            rules=[{"name": "r", "pattern": "(unclosed", "form": "minor", "globs": ["*.txt"]}]
        )


def test_a_toolchain_without_a_source_file_is_rejected():
    with pytest.raises(ConfigError, match="no source_file"):
        toolchain_from_dict("x", {"forms": {"a": "{major}"}})


def test_validate_rejects_a_toolchain_with_no_forms():
    with pytest.raises(ConfigError, match="defines no forms"):
        validate(
            Toolchain(
                name="x",
                source_file=".x",
                forms={},
                rules=(),
                components=1,
                inline_escape="ok",
                ignore_file=".ignore",
            )
        )


# --------------------------------------------------------------------------
# Glob groups
# --------------------------------------------------------------------------


def test_glob_groups_expand(make_repo, run_cli):
    repo = make_repo(
        {
            ".version-drift.toml": """
[toolchain.python]
enabled = false

[toolchain.x]
source_file = ".x-version"
components = 2
[toolchain.x.forms]
minor = "{major}.{minor}"
[toolchain.x.globs]
here = ["*.conf", "*.cnf"]
[[toolchain.x.rules]]
name = "r"
pattern = 'x(\\d+\\.\\d+)'
form = "minor"
globs = ["@here", "*.other"]
""",
            "a.cnf": "x1.0\n",
            "b.other": "x1.0\n",
        },
        version="2.0",
        source=".x-version",
    )
    result = run_cli(repo)
    assert result.code == 1
    assert "a.cnf:1: [r] found '1.0'" in result.err
    assert "b.other:1: [r] found '1.0'" in result.err


def test_an_unknown_glob_group_is_rejected(make_repo, run_cli):
    repo = make_repo(
        {
            ".version-drift.toml": """
[toolchain.x]
source_file = ".x-version"
components = 2
[toolchain.x.forms]
minor = "{major}.{minor}"
[[toolchain.x.rules]]
name = "r"
pattern = 'x(\\d+\\.\\d+)'
form = "minor"
globs = ["@nope"]
"""
        },
        version="2.0",
        source=".x-version",
    )
    result = run_cli(repo)
    assert result.code == 2
    assert "glob group '@nope'" in result.err


# --------------------------------------------------------------------------
# A user-defined toolchain, end to end.
# --------------------------------------------------------------------------


def test_a_repo_defined_toolchain_is_checked(make_repo, run_cli):
    repo = make_repo(
        {
            ".version-drift.toml": NODE_TOOLCHAIN,
            ".nvmrc": "22.22.0\n",
            ".github/workflows/ci.yml": 'steps:\n  - with:\n      node-version: "20.11.0"\n',
            "Dockerfile": "FROM node:20\n",
        }
    )
    result = run_cli(repo, "--toolchain", "node")
    assert result.code == 1
    assert (
        ".github/workflows/ci.yml:3: [setup-node] found '20.11.0', expected 22.22.0" in result.err
    )
    assert "Dockerfile:1: [docker-image] found '20', expected 22" in result.err
    assert "from .nvmrc = 22.22.0" in result.err


def test_toolchains_are_reported_separately(make_repo, run_cli):
    repo = make_repo(
        {
            ".version-drift.toml": NODE_TOOLCHAIN,
            ".nvmrc": "22.22.0\n",
            "Dockerfile": "FROM node:20\nFROM python:3.11.9-slim\n",
        }
    )
    result = run_cli(repo)
    assert result.code == 1
    assert "[node] Version drift" in result.err
    assert "[python] Version drift" in result.err


def test_a_toolchain_without_its_source_file_is_skipped(make_repo, run_cli):
    """A Python repo has no .nvmrc. That is not a finding."""
    repo = make_repo({".version-drift.toml": NODE_TOOLCHAIN, "Dockerfile": "FROM node:20\n"})
    result = run_cli(repo)
    assert result.code == 0, result.err


def test_a_disabled_builtin_is_not_checked(make_repo, run_cli):
    repo = make_repo(
        {
            ".version-drift.toml": "[toolchain.python]\nenabled = false\n",
            "Dockerfile": "FROM python:3.11.9-slim\n",
        }
    )
    # Nothing is left to check, which is an error rather than a silent pass --
    # otherwise disabling everything looks identical to a clean repo.
    result = run_cli(repo)
    assert result.code == 1
    assert "no source-of-truth file found" in result.err


def test_enabling_an_unknown_toolchain_is_rejected(make_repo, run_cli):
    repo = make_repo({".version-drift.toml": "[toolchain.rust]\nenabled = true\n"})
    result = run_cli(repo)
    assert result.code == 2
    assert "enables unknown toolchain 'rust'" in result.err


# --------------------------------------------------------------------------
# What gets swept
# --------------------------------------------------------------------------


def test_untracked_files_are_not_checked(make_repo, run_cli):
    repo = make_repo({"Dockerfile": "FROM python:3.12.13-slim\n"})
    (repo / "scratch.yml").write_text("runtime: python3.11\n", encoding="utf-8")
    assert run_cli(repo).code == 0


def test_the_source_file_is_not_checked_against_itself(make_repo, run_cli):
    repo = make_repo({"Dockerfile": "FROM python:3.12.13-slim\n"})
    assert run_cli(repo).code == 0


def test_a_missing_source_file_is_an_error(make_repo, run_cli):
    repo = make_repo({"Dockerfile": "FROM alpine\n"}, version=None)
    result = run_cli(repo)
    assert result.code == 1
    assert "no source-of-truth file found" in result.err
    assert ".python-version" in result.err


def test_a_malformed_source_file_is_an_error(make_repo, run_cli):
    repo = make_repo({"Dockerfile": "FROM alpine\n"}, version="3.12")
    result = run_cli(repo)
    assert result.code == 2
    assert "at least 3 version components" in result.err


def test_a_binary_file_is_skipped(make_repo, run_cli):
    repo = make_repo({"Dockerfile": "FROM python:3.12.13-slim\n"})
    (repo / "blob.yml").write_bytes(b"\xff\xfe\x00runtime: python3.11\n")
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    assert run_cli(repo).code == 0


# --------------------------------------------------------------------------
# Escape hatches
# --------------------------------------------------------------------------


def test_the_inline_escape_suppresses_a_hit(make_repo, run_cli):
    repo = make_repo(
        {"deploy.sh": "gcloud functions deploy f --runtime python310  # python-version-ok\n"}
    )
    assert run_cli(repo).code == 0


def test_the_ignore_file_suppresses_a_hit(make_repo, run_cli):
    repo = make_repo(
        {
            "vendor/old.yml": "runtime: python3.11\n",
            ".python-version-ignore": "# vendored, tracks its own version\nvendor/*\n",
        }
    )
    assert run_cli(repo).code == 0


def test_ignore_globs_cross_directory_separators(make_repo, run_cli):
    """fnmatch, not gitignore syntax: `*` crosses `/`."""
    repo = make_repo(
        {"vendor/a/b/deep.yml": "runtime: python3.11\n", ".python-version-ignore": "vendor/*\n"}
    )
    assert run_cli(repo).code == 0


def test_fix_leaves_an_escaped_line_untouched(make_repo, run_cli):
    repo = make_repo({"svc.yml": "runtime: python310  # python-version-ok\nother: python3.11\n"})
    run_cli(repo, "--fix")
    assert (repo / "svc.yml").read_text(encoding="utf-8") == (
        "runtime: python310  # python-version-ok\nother: python3.12\n"
    )


# --------------------------------------------------------------------------
# CLI contract
# --------------------------------------------------------------------------


def test_list_toolchains(make_repo, run_cli):
    result = run_cli(make_repo({}), "--list-toolchains")
    assert result.code == 0
    assert "python\t.python-version" in result.out


def test_an_unknown_toolchain_is_rejected(make_repo, run_cli):
    result = run_cli(make_repo({}), "--toolchain", "cobol")
    assert result.code == 2
    assert "unknown toolchain(s) cobol" in result.err


def test_declared_overrides_the_source_file(make_repo, run_cli):
    repo = make_repo({"Dockerfile": "FROM python:3.11.9-slim\n"}, version="3.11.9")
    assert run_cli(repo).code == 0
    result = run_cli(repo, "--toolchain", "python", "--declared", "3.12.13")
    assert result.code == 1
    assert "[docker-image-patch] found '3.11.9', expected 3.12.13" in result.err


def test_declared_works_without_a_source_file(make_repo, run_cli):
    repo = make_repo({"Dockerfile": "FROM python:3.12.13-slim\n"}, version=None)
    assert run_cli(repo, "--toolchain", "python", "--declared", "3.12.13").code == 0


def test_declared_needs_a_single_toolchain(make_repo, run_cli):
    repo = make_repo({".version-drift.toml": NODE_TOOLCHAIN, ".nvmrc": "22.22.0\n"})
    result = run_cli(repo, "--declared", "3.12.13")
    assert result.code == 2
    assert "applies to one toolchain" in result.err


def test_filenames_are_accepted_and_ignored(make_repo, run_cli):
    """pre-commit passes staged filenames to some hooks. The whole repo is swept
    regardless, because editing the source file invalidates untouched files."""
    repo = make_repo({"Dockerfile": "FROM python:3.11.9-slim\n", "unrelated.txt": "hello\n"})
    result = run_cli(repo, "unrelated.txt")
    assert result.code == 1
    assert "Dockerfile:1:" in result.err
