# pre-commit-version-drift

One file says what version your toolchain runs on. Everything else has to agree.

A repo names its Python version in a dozen places — base images, CI matrices,
Lambda runtimes, `requires-python`, ruff's `target-version`, the first line of
CLAUDE.md. Bumps update most of them. The ones they miss don't fail: the build
keeps working on the old version, CI keeps testing something else, and the
README keeps telling new joiners the wrong thing until somebody trips over it
months later.

This hook makes `.python-version` the single source of truth and fails the
commit when anything disagrees.

```
src/email_summarizer/Dockerfile:1: [docker-image-minor] found '3.9', expected 3.12 (from .python-version = 3.12.13)
    FROM public.ecr.aws/lambda/python:3.9
.github/workflows/ci.yaml:25: [setup-python] found '3.14.6', expected 3.12 or 3.12.13 (from .python-version = 3.12.13)
    python-version: '3.14.6'
```

Both of those are real: a Lambda three minor versions behind everything around
it, and a CI job testing on a Python the deployed code never runs.

## Install

Requires Python 3.11+ to run (it uses `tomllib`); it checks repos on any version.

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/jimbofreedman/pre-commit-version-drift
    rev: v0.1.0
    hooks:
      - id: version-drift
```

Then create the source of truth. A full `X.Y.Z` buys the most, because container
registries publish patch tags and a repo pinning only `3.12` can't say which
image it means:

```
3.12.13
```

Install that interpreter locally before committing. pyenv falls through to the
next thing on `PATH` when the requested version is missing, so `python3` **and
`pre-commit`** quietly become different binaries inside the repo, with no error
beyond `pyenv version-name`.

### What may be in that file

You don't write `.python-version` alone — pyenv, uv and Heroku's buildpack all
write it, and they don't agree on a format. All of these are read:

| Contents | Written by |
|---|---|
| `3.12.13` | `pyenv local`, `uv python pin` |
| `3.12` | the same, and **Heroku's recommended form** |
| `3.13t` | uv, free-threaded builds |
| `cpython@3.12`, `cpython-3.12.13-macos-aarch64-none` | uv request syntax |
| `pypy3.10-7.3.12` | pyenv — `3.10` is the language version, `7.3.12` the PyPy release |
| several lines | `pyenv local 3.12 3.11`; the first wins, as pyenv's own shims do |

`system` and `graalpy-24.1.0` are refused by name. `system` declines to pin at
all, so there is nothing to check against; `graalpy-24.1.0` carries a GraalVM
release rather than a language version, and reading its `24` as a major would
flag every pin in the repo.

**Fewer than three components is allowed.** The patch rules don't stand down —
they compare on the components the file does supply, so against `3.12` a stale
`python:3.9.1` is still caught on its `3.9`, while `python:3.12.9` passes because
nothing in the repo can say the patch is wrong. The run says so:

```
.python-version supplies 2 of 3 components, so pins are compared on their first 2:
a wrong patch cannot be detected from it.
```

`--fix` won't rewrite at reduced precision — turning `python:3.9.1` into
`python:3.12` would delete a component the tag needs — so it reports those
instead of touching them.

## What it checks

Three forms are derived from `.python-version`:

| Form | Value | Used for |
|------|-------|----------|
| patch | `3.12.13` | registries publishing patch tags |
| minor | `3.12` | registries publishing only minor tags, interpreter names, runtimes |
| nodot | `312` | `py312`, `python312` tokens |

| Rule | Matches | Form | Files |
|------|---------|------|-------|
| `docker-image-patch` | `python:3.12.13` | patch | Dockerfiles, YAML |
| `docker-image-minor` | `python:3.12` | minor | Dockerfiles, YAML |
| `dockerfile-arg` | `ARG PYTHON_VERSION=3.12.13` | patch (minor ok) | Dockerfiles |
| `sam-build-image` | `build-python3.12` | minor | config |
| `interpreter-or-runtime` | `python3.12` | minor | config, docs |
| `setup-python` | `python-version: "3.12.13"` | patch (minor ok) | YAML |
| `readthedocs-python` | `python: "3.12"` | minor (patch ok) | YAML |
| `requires-python` | `requires-python = ">=3.9"` | floor | `*.toml`, `uv.lock` |
| `python-version-key` | mypy / Pipfile `python_version` | minor (patch ok) | `*.toml`, `*.cfg`, `*.ini`, `Pipfile` |
| `tool-versions` | asdf/mise `python 3.12.13` | patch | `.tool-versions` |
| `runtime-txt` | `python-3.12.13` | patch | `runtime.txt` |
| `conda-python` | `- python=3.12` | minor (patch ok) | `environment.yml` |
| `py-nodot` | `py312` — ruff, tox, SageMaker | nodot | config, `*.py` |
| `python-nodot` | `python312` — GCP runtimes | nodot | config, `*.py` |
| `prose-version` | `Python 3.12` in prose | minor (patch ok) | `*.md` |
| `prose-version-floor` | `Python 3.12+` | floor | `*.md` |

"config" is `*Dockerfile`, `*.dockerfile`, `*.yml`, `*.yaml`, `*.toml`, `*.cfg`,
`*.ini`, `*.tf`, `*.hcl`, `*.sh`, `Pipfile`, `.tool-versions`. Only
**git-tracked** files are swept, and the whole repo is swept every run — editing
the source of truth invalidates pins in files that didn't change.

For `actions/setup-python`, prefer `python-version-file: .python-version`. It is
never flagged, because it cannot drift.

### Pins vs floors

Most rules are equality checks. A **floor** is not: `requires-python = ">=3.9"`
declares the oldest version your package supports, which is a different fact
from the one you develop on. A repo running 3.12.13 may legitimately ship
`">=3.9"`. Only a floor *above* the pin is reported, because that one excludes
the interpreter actually in use.

`--fix` never rewrites a floor. Raising it silently drops support that still works.

### Documentation

`prose-version` is the rule with no equivalent elsewhere, and the one most worth
having. A stale language version in CLAUDE.md or AGENTS.md is invisible to every
other check, and it's the copy a coding agent treats as ground truth — so it
misinforms exactly the reader least able to go and check a Dockerfile.

Capital `P` is required, which is how documentation names the language.
Lowercase is a CLI flag whose argument isn't ours to rewrite (`uv venv --python
3.11`), and `CPython 3.11` is a different word.

## Escape hatches

Some pins genuinely can't track your version — a vendored file, a compatibility
matrix, a base image pinned upstream.

```dockerfile
FROM python:3.9-slim  # python-version-ok: vendor image, pinned upstream
```

Or glob patterns in `.python-version-ignore`. These are **`fnmatch`**, not
gitignore syntax — `*` crosses `/`, so `vendor/*` matches `vendor/a/b.yml`:

```
# historical plan documents; they record what was true when written
docs/plans/*
```

## Autofixing

```yaml
      - id: version-drift
      - id: version-drift-fix
```

`version-drift-fix` is manual-stage, so it never fires on a normal commit:

```sh
pre-commit run --hook-stage manual version-drift-fix --all-files
```

**Review the diff.** It rewrites the matched version and nothing else on the
line, which can produce something valid-looking but wrong. The re-check passes in
every case below, because the version component *is* correct afterwards:

- **A digest-pinned image becomes a lie, silently.** `python:3.9@sha256:ed42…`
  becomes `python:3.12@sha256:ed42…`. The tag moves, the digest doesn't, and when
  both are present the digest wins — so the file claims 3.12 while the build
  still pulls 3.9. Bump the digest by hand, or use `python-version-ok`.
- **A co-located OS tag is left behind.** `python:3.12.10-alpine3.20` becomes
  `python:3.12.13-alpine3.20`, which isn't a real tag. This one at least fails
  the build loudly, and the tool warns about it.
- **CRLF files are rewritten entirely.** Reading normalises `\r\n` to `\n`, so a
  one-token fix rewrites every line.
- **Tracked symlinks are followed**, so a link pointing outside the repo gets
  that file edited in place, where `git status` won't show it.

## Other toolchains

The engine is not Python-specific; the Python rules are a TOML file it ships.
Add your own in `.version-drift.toml` at the repo root:

```toml
[toolchain.node]
source_file = ".nvmrc"
components = 3

[toolchain.node.forms]
full = "{major}.{minor}.{patch}"
major = "{major}"

[toolchain.node.globs]
yaml = ["*.yml", "*.yaml"]

[[toolchain.node.rules]]
name = "setup-node"
pattern = '''node-version:\s*['"]?(\d+\.\d+\.\d+)'''
form = "full"
globs = ["@yaml"]

[[toolchain.node.rules]]
name = "docker-image"
pattern = '(?:^|[/\s])node:(\d+)(?![\d.])'
form = "major"
globs = ["*Dockerfile"]
```

Every toolchain whose source file exists gets checked. A toolchain whose file is
absent is skipped — a Python repo has no `.nvmrc`, and that isn't a finding. If
*no* source file is found at all, that is an error rather than a silent pass.

Patterns must capture exactly one group: the version. Forms are templates over
`{major}`, `{minor}`, `{patch}`. Glob groups declared under `[toolchain.X.globs]`
are referenced as `@name`. `enabled = false` turns off a built-in toolchain.

`components` is how many components the file needs for *every* rule to run at
full precision, not a precondition for running at all — a shorter file degrades
as described above. A toolchain whose source file holds more than a bare version
adds a `source_pattern` capturing the dotted run out of it; the default accepts
`X`, `X.Y`, `X.Y.Z` and a leading `v`.

Bad definitions are rejected at load time, naming the rule — an unknown form, a
pattern with two groups, a floor on an undotted form.

## Known gaps

Deliberately conservative: zero false positives matters more than total coverage,
because a hook that cries wolf gets `--no-verify`'d. It does **not** catch:

- Digests. `python:3.12@sha256:…` is checked on the tag only; the digest can point
  anywhere.
- Anything in `*.json`, including `.devcontainer/devcontainer.json`.
- Space-separated lowercase forms: `uv python install 3.11`.
- Full indirection: `FROM python:${PY}-slim` where `PY` came from outside the
  Dockerfile. The `ARG` form is caught; a build-arg default supplied by CI is not.
- An *absent* pin. It checks that versions agree, never that a given file
  declares one, so a `pyproject.toml` with no `target-version` is not a finding.
- The upper bound of a range: `requires-python = ">=3.12,<3.13"` reads the first
  version only, which is correct — an upper bound shouldn't equal the pin.

## Development

```sh
pip install -e '.[dev]'
pytest
pre-commit try-repo . version-drift --all-files
```

## Licence

MIT
