# pre-commit-version-drift

One file says what version your toolchain runs on. Everything else has to agree.

A repo names its Python version in a dozen places — base images, CI matrices,
Lambda runtimes, `requires-python`, ruff's `target-version`, the first line of
CLAUDE.md or AGENTS.md. Bumps update most of them. The ones they miss don't
fail: the build keeps working on the old version, CI keeps testing something
else, and the README keeps telling new joiners the wrong thing until somebody
trips over it months later.

This hook makes `.python-version` the single source of truth and fails the
commit when anything disagrees.

```
services/worker/Dockerfile:1: [docker-image-minor] found '3.9', expected 3.12 (from .python-version = 3.12.13)
    FROM public.ecr.aws/lambda/python:3.9
.github/workflows/ci.yaml:25: [setup-python] found '3.14.6', expected 3.12 or 3.12.13 (from .python-version = 3.12.13)
    python-version: '3.14.6'
```

Both are the kind of thing it finds: a Lambda three minor versions behind everything around
it, and a CI job testing on a Python the deployed code never runs.

## Install

Requires Python 3.11+ to run (it uses `tomllib`); it checks repos on any version.

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/spotship/pre-commit-version-drift
    rev: v0.2.0
    hooks:
      - id: version-drift
```

The hook ids, their stages and what each runs are defined in
[`.pre-commit-hooks.yaml`](.pre-commit-hooks.yaml), and every key you can set on
them is pre-commit's own [hook schema](https://pre-commit.com/#pre-commit-configyaml---hooks);
neither is repeated here.

Releases are tags, so `pre-commit autoupdate` moves `rev` to the newest one. Don't
use `rev: main`: the hook environment is cached per `rev`, so you would stay
pinned to whichever commit you first installed.

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

`system` (which pins nothing) and `graalpy-24.1.0` (a GraalVM release, not a
language version) are refused by name.

**Fewer than three components is allowed.** The patch rules don't stand down —
they compare on the components the file does supply, so against `3.12` a stale
`python:3.9.1` is still caught on its `3.9`, while `python:3.12.9` passes because
nothing in the repo can say the patch is wrong. The run says so:

```
.python-version supplies 2 of 3 components, so pins are compared on their first 2:
a wrong patch cannot be detected from it.
```

`--fix` reports those rather than rewrite at reduced precision, which would
delete a component the tag needs.

## What it checks: Python

Three forms are derived from `.python-version`:

| Form | Value | Used for |
|------|-------|----------|
| patch | `3.12.13` | registries publishing patch tags |
| minor | `3.12` | registries publishing only minor tags, interpreter names, runtimes |
| nodot | `312` | `py312`, `python312` tokens |

Its rules match those forms against Dockerfiles, CI config, `pyproject.toml`,
`.tool-versions`, `runtime.txt`, `environment.yml`, prose in `*.md`, and more —
a few examples:

```
FROM python:3.12.13-slim          # docker-image-patch (Dockerfiles, YAML)
python-version: "3.12.13"         # setup-python (YAML)
requires-python = ">=3.9"         # requires-python -- a floor, not a pin
This service runs on Python 3.12. # prose-version (*.md)
```

The complete, current list — every rule's pattern, form and files — is defined
in `src/version_drift/toolchains/python.toml`, or printed by
`version-drift --list-toolchains`; this README doesn't keep a second copy of it
to go stale against.

Which files count as "config" is that TOML's `config` glob group. Only
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

`version-drift-fix` is manual-stage (see `.pre-commit-hooks.yaml`), so it never
fires on a normal commit:

```sh
pre-commit run --hook-stage manual version-drift-fix --all-files
```

**A pin written beside a content digest is refused, not rewritten.** A line
like `python:3.9@sha256:ed42…` is reported as drift and left exactly as it was
written. When both are present the digest wins, so moving the tag on its own
would leave the file naming one version while the build pulled another — and the
re-check would pass over it, because the tag it reads is now correct. Update the
tag and the digest together, or put `python-version-ok` on the line.

The refusal is per *match*, not per rule or per file: a bare tag on the next line
is still rewritten, and a digest beside a pin that already agrees is never
mentioned at all. A run that refuses still exits non-zero — the drift is
unresolved, not forgiven.

**Review the diff.** What it does rewrite is the matched version and nothing else
on the line, which can produce something valid-looking but wrong. The re-check
passes in every case below, because the version component *is* correct
afterwards:

- **A co-located OS tag is left behind.** `python:3.12.10-alpine3.20` becomes
  `python:3.12.13-alpine3.20`, which isn't a real tag. This one at least fails
  the build loudly, and the tool warns about it.
- **CRLF files are rewritten entirely.** Reading normalises `\r\n` to `\n`, so a
  one-token fix rewrites every line.
- **Tracked symlinks are followed**, so a link pointing outside the repo gets
  that file edited in place, where `git status` won't show it.

## Node and pnpm

Two more built-in toolchains, with their own sources of truth:

| Toolchain | Source of truth |
|---|---|
| python | `.python-version` |
| node | `.nvmrc` |
| pnpm | `package.json` → `packageManager` |

`.nvmrc` rather than `.node-version`: `actions/setup-node`'s `node-version-file`
reads it, and nvm, fnm, asdf and mise all honour it. `lts/*` and `node` are
refused by name, as `system` is for pyenv.

pnpm has no version file at all — there is no `.pnpm-version` convention. Its pin
is the Corepack `packageManager` field, which pnpm also reads natively, so
getting it wrong changes which package manager runs.

The node and pnpm rules match the same way, against Dockerfiles, Lambda
and Terraform runtimes, CI config, prose, and the structured `package.json`
fields below — a few examples:

```
FROM node:22.23.1-alpine                       # node-image-patch
runtime: nodejs22.x                            # lambda-runtime
"engines": {"node": ">=22 <23"}                # engines-node (parsed, not matched)
packageManager: "pnpm@9.15.9"                  # package-manager-field
```

As with Python, the complete list for either toolchain is in
`src/version_drift/toolchains/node.toml` / `pnpm.toml`, or
`version-drift --list-toolchains`.

Prefer `node-version-file: .nvmrc`, which is never flagged.

### Fields are parsed, not matched

`engines.node`, `engines.pnpm` and `@types/node` are read by parsing
`package.json` and looking up a dotted path, because a regex cannot tell a pin
from a package that happens to be named like one — a dependency called `node`,
or the top-level `"pnpm": { "overrides": … }` config object that sits in the same
file as `engines.pnpm`.

Each of those fields holds a *range*, and the major is the part checked. That is
deliberately not a floor: a caret or a `<23` upper bound **excludes** the next
major, so a repo that moves `.nvmrc` to 24 and leaves `">=22 <23"` behind has an
engines field forbidding the runtime it is pinned to. Reading it as a floor would
pass exactly that case.

Two consequences worth knowing:

- **`--fix` never touches a field.** Rewriting would reserialise the file, and
  the right replacement for a range is a judgement about how wide it should be.
- **There is no inline escape**, because JSON has no comments. A field that
  genuinely cannot track the repo version needs its file in the ignore file.

## ruff and djLint

Two linters get a toolchain of their own, because each is pinned **twice** — once
as a `rev:` in `.pre-commit-config.yaml` and once in the dev dependencies — and
both copies run. When they differ, the same file passes one and fails the other,
and because both tools also *format*, a disagreement rewrites files back and
forth between a commit and a CI run.

| Toolchain | Source of truth | Checks |
|---|---|---|
| ruff | `rev:` of `astral-sh/ruff-pre-commit` | `ruff==X.Y.Z` in `*.txt`, `*.toml`, `*.cfg`, `*.ini` |
| djlint | `rev:` of `Riverside-Healthcare/djLint` | `djlint==X.Y.Z`, same files |

The **rev** is the source because `.pre-commit-config.yaml` is always at the
root, while the dependency pin's home varies. The rev is also the side autoupdate
and Renovate move, so a disagreement is usually the rev lagging: bump it rather
than downgrade the dependency. **`--fix` never rewrites these**, for that reason.

A floating range is reported rather than excused: `ruff>=0.15.6` resolves to
whatever is newest at install time, which is the drift itself.

A repo whose `.pre-commit-config.yaml` lists no ruff hook has no ruff version to
check anything against, so the toolchain is skipped — the same as a Python repo
having no `.nvmrc`.

## Other toolchains

The engine is not language-specific; every built-in toolchain is a TOML file it
ships. Add your own in `.version-drift.toml` at the repo root:

```toml
[toolchain.ruby]
source_file = ".ruby-version"
components = 3

[toolchain.ruby.forms]
patch = "{major}.{minor}.{patch}"
minor = "{major}.{minor}"

[toolchain.ruby.globs]
yaml = ["*.yml", "*.yaml"]

[[toolchain.ruby.rules]]
name = "setup-ruby"
pattern = '''ruby-version:\s*['"]?(\d+\.\d+\.\d+)'''
form = "patch"
globs = ["@yaml"]

[[toolchain.ruby.rules]]
name = "docker-image"
pattern = '(?:^|[/\s])ruby:(\d+\.\d+)(?![\d.])'
form = "minor"
globs = ["*Dockerfile"]
```

Every toolchain whose source exists gets checked. One whose source is absent is
skipped — a Python repo has no `.nvmrc`, and that isn't a finding. For a
field-sourced toolchain the *field's* absence skips it too, which is what keeps
pnpm quiet in the many repos that have a `package.json` only for
semantic-release. If *no* source is found at all, that is an error rather than a
silent pass.

Patterns must capture exactly one group: the version. Glob groups are
referenced as `@name`, and `enabled = false` turns off a built-in toolchain. The
built-in TOMLs in `src/version_drift/toolchains/` are worked examples of every
other key (`source_field`, `source_pattern`, `skip_globs`, `field`, floors), and
a bad definition is rejected at load time with the offending rule named.

## Known gaps

Deliberately conservative: zero false positives matters more than total coverage,
because a hook that cries wolf gets `--no-verify`'d. It does **not** catch:

- Digests, as a *check*. `python:3.12@sha256:…` is read on its tag only, so a
  digest that disagrees with a tag the hook accepts is invisible: resolving one
  would put a network call inside a pre-commit hook. `--fix` refuses to rewrite
  such a line rather than stranding the digest beside a moved tag, but that is a
  guard on the autofix, not coverage of the pin — and the guard reads a literal
  digest, so one held in a build arg (`python:3.12@${PY_DIGEST}`) is not seen.
- JSON, except the `package.json` fields named above. Those are addressed by
  path; nothing scans JSON generally, so `.devcontainer/devcontainer.json` and a
  notebook's `kernelspec` are not read.
- The block form of a multi-version value. `python-version: ['3.11', '3.12']` is
  read, entry by entry, but `python-version: |` with one version per line is not:
  the rules match within a line, and nothing here parses YAML.
- Space-separated lowercase forms: `uv python install 3.11`.
- Full indirection: `FROM python:${PY}-slim` where `PY` came from outside the
  Dockerfile. The `ARG` form is caught; a build-arg default supplied by CI is not.
- An *absent* pin. It checks that versions agree, never that a given file
  declares one, so a `pyproject.toml` with no `target-version` is not a finding.
- The upper bound of a range: `requires-python = ">=3.12,<3.13"` reads the first
  version only, which is correct — an upper bound shouldn't equal the pin.

## Development

Setting up, running the tests, adding a rule and cutting a release:
[CONTRIBUTING.md](CONTRIBUTING.md). The hook makes no network calls and reads no
credentials; its threat model and how to report a vulnerability are in
[SECURITY.md](SECURITY.md).

## Licence

MIT
