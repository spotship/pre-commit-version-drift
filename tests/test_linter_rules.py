"""The ruff and djLint toolchains, and the source-search mechanism they need.

Both read their version out of `.pre-commit-config.yaml`, where it sits on the
line *after* the repo that owns it -- so the source cannot be located by a
per-line pattern, and these cover that path as much as the rules.
"""

from __future__ import annotations

import pytest

CONFIG = """\
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.6
    hooks:
      - id: ruff
      - id: ruff-format

  - repo: https://github.com/Riverside-Healthcare/djLint
    rev: v1.36.4
    hooks:
      - id: djlint-django
"""


def _repo(make_repo, files):
    return make_repo({".pre-commit-config.yaml": CONFIG, **files}, version=None)


# --------------------------------------------------------------------------
# Locating the version inside the source file
# --------------------------------------------------------------------------


def test_the_rev_is_read_from_under_its_own_repo(make_repo, run_cli):
    """Three revs sit in this file. A per-line pattern could not tell which
    belongs to ruff, which is the whole reason source_search spans lines."""
    repo = _repo(make_repo, {"requirements.txt": "ruff==0.8.6\n"})
    result = run_cli(repo, "--toolchain", "ruff")
    assert result.code == 1
    assert "found '0.8.6', expected 0.15.6" in result.err
    assert ".pre-commit-config.yaml = 0.15.6" in result.err


def test_djlint_reads_its_own_rev_not_ruffs(make_repo, run_cli):
    repo = _repo(make_repo, {"requirements.txt": "djlint==1.35.4\n"})
    result = run_cli(repo, "--toolchain", "djlint")
    assert result.code == 1
    assert "found '1.35.4', expected 1.36.4" in result.err


def test_a_matching_pin_passes(make_repo, run_cli):
    repo = _repo(make_repo, {"requirements.txt": "ruff==0.15.6\ndjlint==1.36.4\n"})
    assert run_cli(repo, "--toolchain", "ruff", "--toolchain", "djlint").code == 0


def test_a_repo_without_the_hook_skips_the_toolchain(make_repo, run_cli):
    """No ruff hook means no ruff version to check anything against -- the same
    situation as a Python repo having no .nvmrc, not an error."""
    repo = make_repo(
        {
            ".pre-commit-config.yaml": "repos:\n  - repo: local\n    hooks: []\n",
            ".python-version": "3.12.13\n",
            "requirements.txt": "ruff==0.8.6\n",
        },
        version=None,
    )
    result = run_cli(repo)
    assert result.code == 0
    assert "ruff" not in result.err


# --------------------------------------------------------------------------
# The pins, and what must not be mistaken for one
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "content"),
    [
        ("requirements/local.txt", "ruff==0.8.6  # https://github.com/astral-sh/ruff\n"),
        ("pyproject.toml", '[dependency-groups]\ndev = ["ruff==0.8.6"]\n'),
        ("requirements-dev.txt", "ruff >= 0.8.6\n"),
    ],
)
def test_a_pin_is_found_wherever_it_lives(make_repo, run_cli, rel, content):
    """backend keeps it in requirements/local.txt, ais in a nested
    pyproject.toml. Globs find it; a fixed path could not."""
    repo = _repo(make_repo, {rel: content})
    result = run_cli(repo, "--toolchain", "ruff")
    assert result.code == 1
    assert "[ruff-pin] found '0.8.6'" in result.err


@pytest.mark.parametrize(
    ("rel", "content"),
    [
        ("pyproject.toml", '[tool.ruff]\nline-length = 100\ntarget-version = "py312"\n'),
        ("pyproject.toml", '[tool.ruff.lint]\nselect = ["S101"]  # docs.astral.sh/ruff/rules/\n'),
        ("notes.txt", "we moved to ruff 0.8 last year\n"),
    ],
)
def test_configuration_is_not_a_pin(make_repo, run_cli, rel, content):
    """A config table and a docs URL carry no comparator, and prose is not a
    requirements line. None of these pins a version."""
    repo = _repo(make_repo, {rel: content})
    assert run_cli(repo, "--toolchain", "ruff").code == 0


def test_a_floating_range_is_reported_not_excused(make_repo, run_cli):
    """ais's comment is the specification: a floating range "resolves to whatever
    is newest at lock time and silently drifts away from the hook". The range is
    the defect, so its floor is compared rather than accepted."""
    repo = _repo(make_repo, {"pyproject.toml": 'dev = ["ruff>=0.8"]\n'})
    result = run_cli(repo, "--toolchain", "ruff")
    assert result.code == 1
    assert "[ruff-pin] found '0.8'" in result.err


def test_lockfiles_are_skipped(make_repo, run_cli):
    repo = _repo(make_repo, {"uv.lock": 'name = "ruff"\nversion = "0.8.6"\nruff==0.8.6\n'})
    assert run_cli(repo, "--toolchain", "ruff").code == 0


# --------------------------------------------------------------------------
# --fix must not touch these
# --------------------------------------------------------------------------


def test_fix_reports_but_never_downgrades_the_dependency(make_repo, run_cli):
    """The rev is the source because its path is stable, not because it is the
    authoritative side -- and it is the side autoupdate and Renovate move. So a
    disagreement is usually the rev lagging, and rewriting the dependency to
    match would downgrade the linter. backend's first run was 0.15.10 against a
    rev of 0.8.6: --fix would have taken it back seven minor versions."""
    content = "ruff==0.15.10\n"
    repo = _repo(make_repo, {"requirements.txt": content})
    result = run_cli(repo, "--toolchain", "ruff", "--fix")
    assert result.code == 1
    assert (repo / "requirements.txt").read_text(encoding="utf-8") == content
    assert "[ruff-pin] found '0.15.10'" in result.err


def test_a_config_without_the_hook_is_not_called_absent(make_repo, run_cli):
    """Three absences read differently. ".pre-commit-config.yaml absent" is a lie
    about a config that is there but lists no ruff hook, and sends the reader
    looking for a missing file instead of a missing hook."""
    repo = make_repo(
        {
            ".pre-commit-config.yaml": "repos:\n  - repo: local\n    hooks: []\n",
            ".python-version": "3.12.13\n",
            "requirements.txt": "ruff==0.8.6\n",
            "Dockerfile": "FROM python:3.12.13-slim\n",
        },
        version=None,
    )
    result = run_cli(repo)
    assert result.code == 0
    assert "ruff (.pre-commit-config.yaml names no ruff version)" in result.out
