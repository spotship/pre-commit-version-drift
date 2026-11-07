# AGENTS.md

For what this repo is and how to install and run it, read README.md, not this
file. This file holds only what README.md doesn't say, or what's easy to get
wrong anyway.

## Layout

```
src/version_drift/
  model.py           versions, forms, rules, toolchains; validation at load time
  config.py          loading toolchain TOML (built-ins, then the repo's overrides)
  scan.py            sweeping a repo; the text pass and the structured-field pass
  cli.py             argument parsing, source resolution, reporting
  toolchains/
    python.toml      .python-version          -> 17 rules
    node.toml        .nvmrc                   -> 10 rules
    pnpm.toml        packageManager field     ->  4 rules
    ruff-linter.toml .pre-commit-config.yaml  ->  1 rule (rev of astral-sh/ruff-pre-commit)
    djlint.toml      .pre-commit-config.yaml  ->  1 rule (rev of Riverside-Healthcare/djLint)
tests/
  conftest.py            shared make_repo/run_cli fixtures; the CLI-contract philosophy, stated inline
  test_engine.py         toolchain-agnostic engine, incl. a toolchain defined inline
  test_python_rules.py   the Python rules and the --fix behaviour
  test_node_rules.py     node, pnpm, and the structured-file machinery
  test_linter_rules.py   ruff and djlint, and the source_search (multi-line) machinery
```

Nothing in `model.py`, `config.py`, `scan.py` or `cli.py` is language-specific.
A new language is a TOML file, not a release — keep it that way.

**Don't hand-count a toolchain's rules when documenting it, here or anywhere
else.** Run `version-drift --list-toolchains`, or count `[[rules]]` blocks in
its TOML. This file's own layout table went stale exactly that way: two
toolchains (`ruff-linter.toml`, `djlint.toml`) existed for a release before
this file ever named them.

## Traps

- **Don't rename `ruff-linter.toml` to `ruff.toml`.** That filename is ruff's
  own config name, so ruff would pick it up as configuration for everything in
  the directory and fail with `unknown field \`components\`` (confirmed in
  `toolchains/ruff-linter.toml`'s own header comment). The toolchain is still
  called `ruff` — that name comes from the TOML's `name` key, not the filename.

- **`djlint-pin` and `ruff-pin` have `fixable = false`, permanently, not as an
  oversight to lift.** Both read a dependency pin, but their source of truth is
  the hook's `rev:`, which `pre-commit autoupdate` and Renovate move — a
  disagreement is normally the rev lagging, and `--fix` rewriting the
  dependency to match would downgrade the linter instead. Both TOMLs cite the
  same real incident: a dependency read `ruff==0.15.10`/`djlint==1.36.4`
  against a hook rev of `0.8.6`/`v1.35.4`; `--fix` would have taken either back
  several minor versions.

- **A new toolchain or rule needs an entry in a given `.*-version-ignore` file
  only if its own TOML cites a real-looking token of that toolchain's kind —
  not reflexively in all five.** `toolchains/*` is ignored by all five ignore
  files today (`.python-version-ignore`, `.node-version-ignore`,
  `.pnpm-version-ignore`, `.ruff-version-ignore`, `.djlint-version-ignore`)
  because every built-in toolchain's own definition happens to cite tokens its
  rules match. A toolchain whose definition cites no token of some other
  toolchain's shape doesn't need that toolchain's ignore entry — and one that
  skips the entry it does need will fail the hook on its own definition file.

- **Don't add `README.md` to `.python-version-ignore`.** It's deliberately
  absent so a Python-version bump keeps the README's own prose honest via
  `prose-version`/`prose-version-floor` — that's the whole argument for having
  those two rules. `README.md` *is* listed in `.node-version-ignore` and
  `.pnpm-version-ignore`, for the opposite reason: this repo has no `.nvmrc` or
  `packageManager`, so the README's own node/pnpm documentation
  (`node:22.23.1`, `pnpm@9.15.9`) is the only thing those two toolchains would
  otherwise find to flag here. That asymmetry is intentional; don't resolve it
  either direction.

- **The inline escape is matched per line.** A comment on the line above the
  offending line suppresses nothing — the `<toolchain>-version-ok` comment has
  to sit on the pin's own line.

- **This repo's own `.github/workflows/clickup.yaml` has no draft/ready
  branch.** Unlike the org-wide shared workflow, every `pull_request` event
  here (`opened`, `ready_for_review`, `review_requested`) maps straight to
  `"in review"` — there is no `"self review"` status for a draft PR. Don't
  expect this repo's own PRs to move through ClickUp the way a consumer repo's
  do.

- **Don't bind a test fixture to this repo's own `.python-version`.**
  `tests/conftest.py`'s `DECLARED` constant is a synthetic version, deliberately
  not the live pin — every expectation in the suite (`"3.12"`, `"312"`, the
  `">=3.13"` floor that has to sit above it) is derived from `DECLARED`, so
  tying it to `.python-version` would red-light most of the suite on the next
  minor bump instead of being self-contained.

- **`requires-python` is not the version this is developed on.** It is the
  oldest interpreter a consumer can install it on. Renovate has
  `requires-python` updates disabled outright (see `renovate.json`) for exactly
  this reason — raising it breaks consumers who install on an older floor.

- **Don't let `install-floor`'s pinned Python version drift from the
  `requires-python` floor it exists to guard.** That job's `setup-python` step
  intentionally sits below `.python-version` and carries its own
  `python-version-ok` escape — the escape is what let it drift silently once
  before: `renovate.json` records that an unguarded bump took it from `3.11` to
  `3.15.1` on the `actions/python-versions` datasource and the floor went
  untested until someone noticed. `renovate.json` now disables major/minor
  updates to that datasource specifically to stop it happening again; don't
  remove that rule to "let Renovate keep things current."

## Testing this repo

Every test drives the real CLI against a throwaway git repo. The CLI contract —
exit code, stderr text, `file:line` reporting — is what consumers experience;
asserting on the rule tuples would test something nobody runs.

**The unit tests cannot catch a broken `entry:`.** They import the package
directly, so they pass with the console script misnamed or missing. Two CI jobs
cover what they can't, and both are load-bearing:

- `pre-commit try-repo . version-drift --all-files` — the packaged path.
- A genuine *separate* consumer repo. `try-repo` runs a `language: python` hook
  with cwd set to the consumer, and under `try-repo` the consumer **is** this
  repo, so a slash-bearing `entry:` still resolves here while failing for every
  real consumer.

`install-floor` runs on `requires-python`'s floor rather than `.python-version`,
because pre-commit builds the hook venv from whatever interpreter pre-commit
itself runs on, which in a consumer with no `default_language_version` is
routinely older than the pin.
