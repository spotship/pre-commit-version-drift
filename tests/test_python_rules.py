"""The built-in Python ruleset.

One case per rule, then the shapes that are easy to get wrong: floors, prose,
and the false positives that would make the hook noise people learn to skip.
"""

from __future__ import annotations

import pytest

# --------------------------------------------------------------------------
# One case per rule. Each asserts the rule name, the file:line and both version
# strings, so a regression that reports the wrong rule still fails.
# --------------------------------------------------------------------------

RULE_CASES = [
    ("docker-image-patch", "Dockerfile", "FROM python:3.11.9-slim\n", 1, "3.11.9"),
    (
        "docker-image-minor",
        "lambda.dockerfile",
        "FROM public.ecr.aws/lambda/python:3.11\n",
        1,
        "3.11",
    ),
    (
        "dockerfile-arg",
        "Dockerfile",
        "ARG PYTHON_VERSION=3.11.9\nFROM python:${PYTHON_VERSION}\n",
        1,
        "3.11.9",
    ),
    (
        "sam-build-image",
        "template.yaml",
        "Metadata:\n  BuildImage: public.ecr.aws/sam/build-python3.11\n",
        2,
        "3.11",
    ),
    ("interpreter-or-runtime", "serverless.yml", "provider:\n  runtime: python3.11\n", 2, "3.11"),
    (
        "setup-python",
        ".github/workflows/b.yaml",
        'steps:\n  - with:\n      python-version: "3.11"\n',
        3,
        "3.11",
    ),
    ("readthedocs-python", ".readthedocs.yml", 'build:\n  tools:\n    python: "3.11"\n', 3, "3.11"),
    ("python-version-key", "setup.cfg", '[mypy]\npython_version = "3.11"\n', 2, "3.11"),
    ("tool-versions", ".tool-versions", "python 3.11.9\nnodejs 22.22.0\n", 1, "3.11.9"),
    ("runtime-txt", "runtime.txt", "python-3.11.9\n", 1, "3.11.9"),
    ("conda-python", "environment.yml", "dependencies:\n  - python=3.11\n", 2, "3.11"),
    ("py-nodot", "ruff.toml", 'target-version = "py311"\n', 1, "311"),
    ("python-nodot", "main.tf", 'resource "x" "y" {\n  runtime = "python311"\n}\n', 2, "311"),
    (
        "prose-version",
        "CLAUDE.md",
        "# Repo\n\n- **Language**: Python 3.11, Django 5.2\n",
        3,
        "3.11",
    ),
]


@pytest.mark.parametrize(
    ("rule", "rel", "content", "lineno", "found"),
    RULE_CASES,
    ids=[case[0] for case in RULE_CASES],
)
def test_each_rule_detects_drift(make_repo, run_cli, rule, rel, content, lineno, found):
    result = run_cli(make_repo({rel: content}))
    assert result.code == 1, result.out
    wanted = f"{rel}:{lineno}: [{rule}] found {found!r},"
    assert wanted in result.err, f"missing {wanted!r} in:\n{result.err}"


def test_a_clean_repo_passes(make_repo, run_cli):
    repo = make_repo(
        {
            "Dockerfile": "FROM python:3.12.13-slim\nRUN python3.12 -m pip install -U pip\n",
            "pyproject.toml": '[project]\nrequires-python = ">=3.9"\n\n[tool.ruff]\ntarget-version = "py312"\n',
            "README.md": "Runs on Python 3.12. Requires Python 3.9+.\n",
            ".tool-versions": "python 3.12.13\n",
        }
    )
    result = run_cli(repo)
    assert result.code == 0, result.err
    assert result.out == ""


# --------------------------------------------------------------------------
# Precision: some pins are legitimate at more than one precision.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "content"),
    [
        (".github/workflows/b.yaml", 'steps:\n  - with:\n      python-version: "3.12"\n'),
        (".readthedocs.yml", 'build:\n  tools:\n    python: "3.12.13"\n'),
        ("README.md", "Built against Python 3.12.13.\n"),
        ("Dockerfile", "ARG PYTHON_VERSION=3.12\n"),
    ],
    ids=["setup-python-minor", "readthedocs-patch", "prose-patch", "arg-minor"],
)
def test_alternate_precisions_are_accepted(make_repo, run_cli, rel, content):
    assert run_cli(make_repo({rel: content})).code == 0


# --------------------------------------------------------------------------
# Container images in YAML. Compose services and `container: image:` pin base
# images too, and the digit-after-colon test is what keeps this rule off
# readthedocs' `python: "3.12"` without the checker knowing which file is which.
# --------------------------------------------------------------------------


def test_a_compose_image_is_checked(make_repo, run_cli):
    repo = make_repo({"docker-compose.yml": "services:\n  app:\n    image: python:3.11.9-slim\n"})
    result = run_cli(repo)
    assert result.code == 1
    assert "docker-compose.yml:3: [docker-image-patch] found '3.11.9'" in result.err


def test_a_yaml_python_key_is_not_read_as_an_image(make_repo, run_cli):
    """`python: "3.12"` is readthedocs; `python:3.12` is a tag. One space apart."""
    repo = make_repo({".readthedocs.yml": 'build:\n  tools:\n    python: "3.12"\n'})
    assert run_cli(repo).code == 0


# --------------------------------------------------------------------------
# Floors. A floor declares the oldest version supported, which is a different
# fact from the one the repo runs on.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "content"),
    [
        ("pyproject.toml", '[project]\nrequires-python = ">=3.8"\n'),
        ("uv.lock", 'version = 1\nrequires-python = ">=3.12"\n'),
        ("README.md", "- Python 3.9+\n"),
        # Must compare as 3.12, not (3, 12, 13) against (3, 12) -- which would
        # order above the pin and report the pin itself as drift.
        ("README.md", "- Python 3.12.13+\n"),
    ],
    ids=["requires-python", "uv-lock", "prose-plus", "prose-plus-patch"],
)
def test_a_floor_at_or_below_the_pin_passes(make_repo, run_cli, rel, content):
    result = run_cli(make_repo({rel: content}))
    assert result.code == 0, result.err


@pytest.mark.parametrize(
    ("rel", "rule", "content"),
    [
        ("pyproject.toml", "requires-python", '[project]\nrequires-python = ">=3.13"\n'),
        ("uv.lock", "requires-python", 'version = 1\nrequires-python = ">=3.13"\n'),
        ("README.md", "prose-version-floor", "- Python 3.13+\n"),
    ],
    ids=["requires-python", "uv-lock", "prose-plus"],
)
def test_a_floor_above_the_pin_is_reported(make_repo, run_cli, rel, rule, content):
    result = run_cli(make_repo({rel: content}))
    assert result.code == 1
    assert f"[{rule}] floor '3.13' is above .python-version (3.12.13)" in result.err


# --------------------------------------------------------------------------
# Documentation prose. A stale "Python 3.12" in CLAUDE.md is drift no other rule
# can see, and it is the copy a coding agent trusts.
# --------------------------------------------------------------------------


def test_prose_at_the_end_of_a_sentence_is_caught(make_repo, run_cli):
    """The lookahead rejects a dot that *continues* a version, not a full stop.
    Rejecting both would blind the rule to most English sentences."""
    result = run_cli(make_repo({"README.md": "This service runs on Python 3.11.\n"}))
    assert result.code == 1
    assert "README.md:1: [prose-version] found '3.11'" in result.err


def test_prose_plus_is_never_read_as_a_pin(make_repo, run_cli):
    """Regression: an unanchored pattern backtracks "Python 3.12+" into a match
    of "3.1" and reports the repo's own version as drift."""
    result = run_cli(make_repo({"README.md": "- Python 3.12+\n"}))
    assert result.code == 0, result.err
    assert "3.1'" not in result.err


def test_prose_is_case_sensitive(make_repo, run_cli):
    """Lowercase `python` before a space-separated number is a CLI flag whose
    argument is not ours to rewrite, and `CPython` is a different word."""
    repo = make_repo(
        {
            "README.md": "Run `uv venv --python 3.11` or `uv python install 3.11`.\nCPython 3.11 changed this.\n"
        }
    )
    result = run_cli(repo)
    assert result.code == 0, result.err


def test_a_stale_command_in_markdown_is_caught(make_repo, run_cli):
    """Unlike prose, `python3.11 -m pip` in a README is a command that breaks."""
    result = run_cli(make_repo({"README.md": "Run `python3.11 -m pip install .`.\n"}))
    assert result.code == 1
    assert "[interpreter-or-runtime] found '3.11'" in result.err


def test_nodot_tokens_in_markdown_stay_silent(make_repo, run_cli):
    """Prose quotes these tokens to *describe* them -- any document explaining
    the rules lists `py39` as an example of what they match. Sweeping markdown
    for them makes such a document unwritable."""
    repo = make_repo(
        {"README.md": "| `py-nodot` | `py312`, `py39` |\n| `python-nodot` | `python39` |\n"}
    )
    result = run_cli(repo)
    assert result.code == 0, result.err


# --------------------------------------------------------------------------
# No false positives. These shapes appear all over real repos and must stay
# silent, or the hook becomes noise everyone learns to ignore.
# --------------------------------------------------------------------------

FALSE_POSITIVES = {
    "setup-python-file": (
        ".github/workflows/a.yaml",
        "steps:\n  - with:\n      python-version-file: .python-version\n",
    ),
    "node-version": (
        ".github/workflows/b.yaml",
        "steps:\n  - with:\n      node-version: 22.22.0\n",
    ),
    "runs-on": (".github/workflows/c.yaml", "jobs:\n  t:\n    runs-on: ubuntu-22.04\n"),
    "node-image": ("node.dockerfile", "FROM node:20.11\n"),
    "default-language-version": (
        ".pre-commit-config.yaml",
        "default_language_version:\n  python: python3.12\n",
    ),
    "package-with-digits": (
        "pyproject.toml",
        '[project]\ndependencies = ["numpy311", "scipy>=1.11"]\n',
    ),
    "unrelated-version": ("app.toml", 'version = "1.312.0"\n'),
    "longer-nodot-tokens": ("deploy.sh", "echo kepy301 py3123 python3123\n"),
    "conda-outside-env-file": ("values.yml", "config:\n  python = 3.11\n"),
}


@pytest.mark.parametrize(
    ("rel", "content"), list(FALSE_POSITIVES.values()), ids=list(FALSE_POSITIVES)
)
def test_no_false_positives(make_repo, run_cli, rel, content):
    result = run_cli(make_repo({rel: content}))
    assert result.code == 0, f"false positive:\n{result.err}"


def test_one_pin_matching_two_rules_is_reported_once(make_repo, run_cli):
    """`build-python3.11` satisfies sam-build-image and interpreter-or-runtime at
    the same offsets; the count somebody reads must not double."""
    result = run_cli(
        make_repo({"template.yaml": "BuildImage: public.ecr.aws/sam/build-python3.11\n"})
    )
    assert result.code == 1
    assert "1 pin(s) disagree" in result.err
    assert "[sam-build-image]" in result.err
    assert "[interpreter-or-runtime]" not in result.err


# --------------------------------------------------------------------------
# --fix
# --------------------------------------------------------------------------


def test_fix_rewrites_then_recheck_passes(make_repo, run_cli):
    repo = make_repo(
        {
            "Dockerfile": "FROM python:3.11.9-slim\nRUN python3.11 -m pip install -U pip\n",
            "pyproject.toml": '[project]\nrequires-python = ">=3.11"\n\n[tool.ruff]\ntarget-version = "py311"\n',
        }
    )
    fixed = run_cli(repo, "--fix")
    assert fixed.code == 1, "a run that modifies files must fail, per pre-commit convention"
    assert "Rewrote version pins in 2 file(s)" in fixed.out

    assert (repo / "Dockerfile").read_text(encoding="utf-8") == (
        "FROM python:3.12.13-slim\nRUN python3.12 -m pip install -U pip\n"
    )
    # The floor is left exactly as written: ">=3.11" says the package still
    # supports 3.11, which running 3.12.13 does not contradict.
    assert (repo / "pyproject.toml").read_text(encoding="utf-8") == (
        '[project]\nrequires-python = ">=3.11"\n\n[tool.ruff]\ntarget-version = "py312"\n'
    )
    assert run_cli(repo).code == 0


def test_fix_rewrites_prose_but_not_a_prose_floor(make_repo, run_cli):
    repo = make_repo({"README.md": "Requires Python 3.11 or newer. Python 3.9+ is dead.\n"})
    assert run_cli(repo, "--fix").code == 1
    assert (repo / "README.md").read_text(encoding="utf-8") == (
        "Requires Python 3.12 or newer. Python 3.9+ is dead.\n"
    )


def test_fix_warns_that_co_located_tags_are_not_rewritten(make_repo, run_cli):
    repo = make_repo({"Dockerfile": "FROM python:3.12.10-alpine3.20\n"})
    fixed = run_cli(repo, "--fix")
    assert fixed.code == 1
    assert "-alpine3.20" in fixed.out
    # Only the version component moves, which is exactly why the warning exists:
    # 3.12.13 ships no alpine3.20 tag.
    assert (repo / "Dockerfile").read_text(encoding="utf-8") == "FROM python:3.12.13-alpine3.20\n"


def test_fix_reports_a_bad_floor_even_while_rewriting_other_pins(make_repo, run_cli):
    """A floor --fix cannot touch must not be swallowed by a successful rewrite
    elsewhere in the same run."""
    repo = make_repo(
        {
            "Dockerfile": "FROM python:3.11.9-slim\n",
            "pyproject.toml": '[project]\nrequires-python = ">=3.13"\n',
        }
    )
    result = run_cli(repo, "--fix")
    assert result.code == 1
    assert "Rewrote version pins in 1 file(s)" in result.out
    assert "[requires-python] floor '3.13'" in result.err


def test_fix_on_a_clean_repo_changes_nothing(make_repo, run_cli):
    repo = make_repo({"Dockerfile": "FROM python:3.12.13-slim\n"})
    result = run_cli(repo, "--fix")
    assert result.code == 0
    assert result.out == ""
