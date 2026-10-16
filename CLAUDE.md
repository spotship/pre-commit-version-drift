# pre-commit-version-drift

A pre-commit hook that fails the commit when any pinned toolchain version in a
repo disagrees with that toolchain's source-of-truth file. Published for other
repos to consume, so the hook's *packaged* behaviour is the product — not just
the library behind it.

## Layout

```
src/version_drift/
  model.py          versions, forms, rules, toolchains; validation at load time
  config.py         loading toolchain TOML (built-ins, then the repo's overrides)
  scan.py           sweeping a repo; the text pass and the structured-field pass
  cli.py            argument parsing, source resolution, reporting
  toolchains/
    python.toml     .python-version   -> 17 rules
    node.toml       .nvmrc            -> 10 rules
    pnpm.toml       packageManager    ->  4 rules
tests/
  test_engine.py        toolchain-agnostic engine, incl. a toolchain defined inline
  test_python_rules.py  the Python rules and the --fix behaviour
  test_node_rules.py    node, pnpm, and the structured-file machinery
```

Nothing in `model.py`, `config.py`, `scan.py` or `cli.py` is language-specific.
A new language is a TOML file, not a release — keep it that way.

## Common commands

```sh
task init         # pip install -e '.[dev]' + pre-commit install
task lint         # ruff check + ruff format --check
task test         # pytest
task check        # run this repo's own hook against itself
task try-repo     # drive the packaged hook the way a consumer gets it
```

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

## Gotchas

- **`requires-python` is not the version this is developed on.** It is the oldest
  interpreter a consumer can install it on. Raising it breaks those consumers;
  Renovate is configured not to touch it.
- **The repo checks itself**, and `README.md` is deliberately not in
  `.python-version-ignore` — so the prose rules keep the documentation honest on
  the next bump. `src/version_drift/toolchains/*` *is* ignored by all three
  ignore files, because a rule's own definition cites the tokens it matches.
- **The inline escape is matched per line.** A comment above the offending line
  suppresses nothing.
- **Structured fields have no inline escape**, JSON having no comments; the only
  way out is the ignore file.

## Versioning and release

Conventional commits, semantic-release off `main`. This repo is consumed by
`rev:` in other repos' `.pre-commit-config.yaml`, so a tag is a published
interface: consumers pin it and `pre-commit autoupdate` moves them to it.
