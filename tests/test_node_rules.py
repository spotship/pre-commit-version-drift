"""The node and pnpm toolchains, and the structured-file machinery they need.

package.json is where most of a Node repo's version pins live, and it cannot be
swept by regex -- so these cover the parsing path as much as the rules.
"""

from __future__ import annotations

import json

import pytest

NVMRC = "22.23.1"


def _pkg(**fields) -> str:
    return json.dumps(fields, indent=2) + "\n"


# --------------------------------------------------------------------------
# Node: the text rules
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "content", "rule", "found"),
    [
        (".github/workflows/ci.yaml", "        node-version: '20.1.0'\n", "setup-node", "20.1.0"),
        (".github/workflows/cd.yaml", '      node_version: "20"\n', "node-version-input", "20"),
        (".github/workflows/cd.yaml", '      node_version: "20.x"\n', "node-version-input", "20"),
        ("Dockerfile", "FROM node:20.1.0-alpine\n", "node-image-patch", "20.1.0"),
        ("Dockerfile", "FROM node:20-alpine\n", "node-image-major", "20"),
        ("serverless.yml", "  runtime: nodejs20.x\n", "lambda-runtime", "20"),
        ("main.tf", '  runtime = "nodejs20"\n', "gcp-runtime", "20"),
        ("README.md", "- Node.js 20.x is required\n", "prose-node", "20"),
    ],
)
def test_node_pins_are_checked(make_repo, run_cli, rel, content, rule, found):
    repo = make_repo({rel: content}, version=NVMRC, source=".nvmrc")
    result = run_cli(repo, "--toolchain", "node")
    assert result.code == 1
    assert f"[{rule}] found {found!r}" in result.err


def test_node_version_file_is_never_flagged(make_repo, run_cli):
    """`node-version-file: .nvmrc` cannot drift, so it is not a pin."""
    repo = make_repo(
        {".github/workflows/ci.yaml": "        node-version-file: .nvmrc\n"},
        version=NVMRC,
        source=".nvmrc",
    )
    assert run_cli(repo, "--toolchain", "node").code == 0


def test_a_dependency_named_parse_node_version_is_not_a_pin(make_repo, run_cli):
    """The lookbehind on setup-node. This package is real and sits in
    pnpm-lock.yaml, which is also why lockfiles are skipped outright."""
    repo = make_repo(
        {"deps.yaml": "      parse-node-version: 1.0.1\n"}, version=NVMRC, source=".nvmrc"
    )
    assert run_cli(repo, "--toolchain", "node").code == 0


def test_lockfiles_are_skipped(make_repo, run_cli):
    repo = make_repo(
        {"pnpm-lock.yaml": "  node-version: '18.0.0'\n"}, version=NVMRC, source=".nvmrc"
    )
    assert run_cli(repo, "--toolchain", "node").code == 0


# --------------------------------------------------------------------------
# Node: the structured rules
# --------------------------------------------------------------------------


def test_engines_node_is_read_by_parsing(make_repo, run_cli):
    repo = make_repo(
        {"package.json": _pkg(engines={"node": ">=20 <21"})}, version=NVMRC, source=".nvmrc"
    )
    result = run_cli(repo, "--toolchain", "node")
    assert result.code == 1
    assert "[engines-node] found '20', expected 22" in result.err
    assert '"engines.node"' in result.err


def test_engines_node_agreeing_on_the_major_passes(make_repo, run_cli):
    """ ">=22 <23" against 22.23.1: the range's own bounds are not the pin's
    business, only the major they start at."""
    repo = make_repo(
        {"package.json": _pkg(engines={"node": ">=22 <23"})}, version=NVMRC, source=".nvmrc"
    )
    assert run_cli(repo, "--toolchain", "node").code == 0


def test_types_node_tracks_the_runtime_major(make_repo, run_cli):
    repo = make_repo(
        {"package.json": _pkg(devDependencies={"@types/node": "^20.1.0"})},
        version=NVMRC,
        source=".nvmrc",
    )
    result = run_cli(repo, "--toolchain", "node")
    assert result.code == 1
    assert "[types-node-dev] found '20'" in result.err


def test_a_dependency_called_node_is_not_an_engines_constraint(make_repo, run_cli):
    """Why these are parsed rather than matched. A regex over the text would read
    the dependency's version as a Node pin."""
    repo = make_repo(
        {"package.json": _pkg(dependencies={"node": "18.0.0", "nodemailer": "^6.4.0"})},
        version=NVMRC,
        source=".nvmrc",
    )
    assert run_cli(repo, "--toolchain", "node").code == 0


def test_an_absent_field_is_not_a_finding(make_repo, run_cli):
    """It checks that versions agree, never that a file declares one."""
    repo = make_repo({"package.json": _pkg(name="x")}, version=NVMRC, source=".nvmrc")
    assert run_cli(repo, "--toolchain", "node").code == 0


def test_a_field_of_the_wrong_shape_is_skipped(make_repo, run_cli):
    """`"engines": "22"` is not the object this path addresses. Missing beats
    stringifying a value into something a pattern might match."""
    repo = make_repo({"package.json": _pkg(engines="22")}, version=NVMRC, source=".nvmrc")
    assert run_cli(repo, "--toolchain", "node").code == 0


def test_unparseable_json_is_somebody_elses_failure(make_repo, run_cli):
    repo = make_repo({"package.json": "{ not json\n"}, version=NVMRC, source=".nvmrc")
    assert run_cli(repo, "--toolchain", "node").code == 0


def test_fix_never_rewrites_a_structured_field(make_repo, run_cli):
    """Rewriting would reserialise the file, and the right replacement for a
    range is a judgement about its width, not a version."""
    content = _pkg(engines={"node": ">=20 <21"})
    repo = make_repo({"package.json": content}, version=NVMRC, source=".nvmrc")
    result = run_cli(repo, "--toolchain", "node", "--fix")
    assert result.code == 1
    assert (repo / "package.json").read_text(encoding="utf-8") == content


# --------------------------------------------------------------------------
# pnpm: a toolchain whose source of truth is a field
# --------------------------------------------------------------------------


def test_pnpm_reads_its_version_from_package_manager(make_repo, run_cli):
    repo = make_repo(
        {
            "package.json": _pkg(packageManager="pnpm@9.15.9"),
            "README.md": "run `corepack prepare pnpm@9.7.0 --activate`\n",
        },
        version=None,
    )
    result = run_cli(repo, "--toolchain", "pnpm")
    assert result.code == 1
    assert "[pnpm-spec] found '9.7.0'" in result.err
    assert "from package.json = pnpm@9.15.9" in result.err


def test_a_package_json_without_package_manager_skips_pnpm(make_repo, run_cli):
    """Every repo in a polyglot estate has a package.json for semantic-release,
    and only the ones using pnpm set this field. Its absence means "not this
    toolchain", not "nothing to check"."""
    repo = make_repo(
        {
            "package.json": _pkg(name="x", devDependencies={"semantic-release": "^24.0.0"}),
            "README.md": "run `corepack prepare pnpm@9.7.0 --activate`\n",
        },
        version=None,
    )
    result = run_cli(repo, "--toolchain", "pnpm")
    assert result.code == 1
    assert "no source-of-truth file found" in result.err
    assert "package.json (packageManager)" in result.err


def test_the_root_package_json_is_still_checked_for_its_other_fields(make_repo, run_cli):
    """A field-sourced toolchain reads its own source file. Only the field it
    took the version from is exempt -- the rest can drift from it freely."""
    repo = make_repo(
        {"package.json": _pkg(packageManager="pnpm@9.15.9", engines={"pnpm": "^8.0.0"})},
        version=None,
    )
    result = run_cli(repo, "--toolchain", "pnpm")
    assert result.code == 1
    assert "[engines-pnpm] found '8'" in result.err


def test_the_source_field_cannot_disagree_with_itself(make_repo, run_cli):
    """packageManager in the *root* is the source. The rule that checks the same
    field in workspace package.json files must not turn on it."""
    repo = make_repo({"package.json": _pkg(packageManager="pnpm@9.15.9")}, version=None)
    assert run_cli(repo, "--toolchain", "pnpm").code == 0


def test_a_workspace_package_manager_field_is_checked(make_repo, run_cli):
    """pnpm honours only the root's, so a stale copy in a package misleads every
    reader without changing anything."""
    repo = make_repo(
        {
            "package.json": _pkg(packageManager="pnpm@9.15.9"),
            "apps/web/package.json": _pkg(packageManager="pnpm@8.6.0"),
        },
        version=None,
    )
    result = run_cli(repo, "--toolchain", "pnpm")
    assert result.code == 1
    assert "[package-manager-field] found '8.6.0'" in result.err


def test_a_corepack_integrity_hash_is_not_part_of_the_version(make_repo, run_cli):
    repo = make_repo(
        {"package.json": _pkg(packageManager="pnpm@9.15.9+sha512.abc123", engines={"pnpm": "^9"})},
        version=None,
    )
    assert run_cli(repo, "--toolchain", "pnpm").code == 0


def test_a_bad_declared_is_blamed_on_the_flag_not_the_file(make_repo, run_cli):
    """`--declared` stands in for the field's raw value ("pnpm@10.1.0"). Passing
    a bare version is easy, and saying package.json said it would be a lie."""
    repo = make_repo({"package.json": _pkg(packageManager="pnpm@9.15.9")}, version=None)
    result = run_cli(repo, "--toolchain", "pnpm", "--declared", "10.1.0")
    assert result.code == 2
    assert "--declared says '10.1.0'" in result.err


# --------------------------------------------------------------------------
# Several toolchains at once
# --------------------------------------------------------------------------


def test_each_toolchain_names_its_own_escape_hatch(make_repo, run_cli):
    """A pnpm finding pointed at `node-version-ok` sends the reader to a hatch
    that will not open."""
    repo = make_repo(
        {
            "package.json": _pkg(packageManager="pnpm@9.15.9", engines={"pnpm": "^8.0.0"}),
            ".nvmrc": f"{NVMRC}\n",
            "Dockerfile": "FROM node:20-alpine\n",
        },
        version=None,
    )
    result = run_cli(repo)
    assert result.code == 1
    assert "[pnpm] Fix the pins, or add an inline `pnpm-version-ok`" in result.err
    assert "[node] Fix the pins, or add an inline `node-version-ok`" in result.err


def test_a_python_repo_is_untouched_by_the_node_toolchains(make_repo, run_cli):
    """.nvmrc absent and packageManager absent: both skip, and the Python rules
    still run. Adding toolchains must not cost existing consumers anything."""
    repo = make_repo(
        {
            "package.json": _pkg(name="x", devDependencies={"semantic-release": "^24.0.0"}),
            "Dockerfile": "FROM python:3.11.9-slim\n",
        }
    )
    result = run_cli(repo)
    assert result.code == 1
    assert "[docker-image-patch] found '3.11.9'" in result.err
    assert "node" not in result.err
    assert "pnpm" not in result.err


def test_a_package_manager_field_cannot_pass_a_repo_off_as_checked(make_repo, run_cli):
    """The ais shape: a Python repo keeping `packageManager` for semantic-release.

    pnpm resolving there satisfies the "some source of truth exists" test on its
    own, which would turn the loud "no source of truth" error into an exit 0 that
    reads as a clean bill of health while no Python pin has been looked at.
    """
    repo = make_repo(
        {
            "package.json": _pkg(packageManager="pnpm@9.15.9"),
            "Dockerfile": "FROM python:3.11.9-slim\n",
        },
        version=None,
    )
    result = run_cli(repo)
    assert result.code == 0
    assert "Not checked: python (.python-version absent)" in result.out
    assert "nothing is checking them" in result.out


def test_a_language_the_repo_does_not_use_is_not_mentioned(make_repo, run_cli):
    """The other half of that: a Python repo must not be lectured about .nvmrc.
    Noise on every commit is how a hook earns a --no-verify."""
    repo = make_repo({"Dockerfile": "FROM python:3.12.13-slim\n"})
    result = run_cli(repo)
    assert result.code == 0
    assert result.out == ""


def test_an_absent_source_file_is_not_described_as_a_missing_field(make_repo, run_cli):
    """ "package.json has no packageManager" is only true when the file is there.

    Said of a repo with no package.json at all, it sends the reader looking for a
    field in a file that does not exist. Needs a second toolchain that *does*
    resolve, because with nothing resolving the louder "no source of truth at
    all" error covers the same ground first.
    """
    repo = make_repo(
        {"README.md": "activate with `corepack prepare pnpm@9.7.0`\n"},
        version=NVMRC,
        source=".nvmrc",
    )
    result = run_cli(repo)
    assert result.code == 0
    assert "Not checked: pnpm (package.json absent)" in result.out
