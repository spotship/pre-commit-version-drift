# pre-commit-version-drift

What this is and how to install it: README.md. Commands, adding a rule and
cutting a release: CONTRIBUTING.md. This file holds only what those don't say,
or what's easy to get wrong anyway.

## Layout

```
src/version_drift/
  model.py          versions, forms, rules, toolchains; validation at load time
  config.py         loading toolchain TOML (built-ins, then the repo's overrides)
  scan.py           sweeping a repo; the text pass and the structured-field pass
  cli.py            argument parsing, source resolution, reporting
  toolchains/
    python.toml       .python-version
    node.toml         .nvmrc
    pnpm.toml         packageManager
    ruff-linter.toml  rev of astral-sh/ruff-pre-commit
    djlint.toml       rev of Riverside-Healthcare/djLint
tests/
  conftest.py           make_repo/run_cli fixtures
  test_engine.py        toolchain-agnostic engine, incl. a toolchain defined inline
  test_python_rules.py  the Python rules and the --fix behaviour
  test_node_rules.py    node, pnpm, and the structured-file machinery
  test_linter_rules.py  ruff and djlint, and the multi-line source search
```

Nothing in `model.py`, `config.py`, `scan.py` or `cli.py` is language-specific.
A new language is a TOML file, not a release — keep it that way.

Don't write a rule count into any document. Run `version-drift --list-toolchains`
or count `[[rules]]` in the TOML; the last hand-written counts here went stale.

## Testing this repo

Every test drives the real CLI against a throwaway git repo. The CLI contract —
exit code, stderr text, `file:line` reporting — is what consumers experience;
asserting on the rule tuples would test something nobody runs.

**The unit tests cannot catch a broken `entry:`.** They import the package
directly, so they pass with the console script misnamed or missing. Two steps
in CI's `hook` job cover what they can't, and both are load-bearing:

- `pre-commit try-repo . version-drift --all-files` — the packaged path.
- A genuine *separate* consumer repo. `try-repo` runs a `language: python` hook
  with cwd set to the consumer, and under `try-repo` the consumer **is** this
  repo, so a slash-bearing `entry:` still resolves here while failing for every
  real consumer.

`install-floor` runs on `requires-python`'s floor rather than `.python-version`,
because pre-commit builds the hook venv from whatever interpreter pre-commit
itself runs on, which in a consumer with no `default_language_version` is
routinely older than the pin.

`tests/conftest.py`'s `DECLARED` is a synthetic version, not the live
`.python-version`. Every expectation is derived from it; don't tie it to the
pin, or the next minor bump turns most of the suite red.

## Gotchas

- **`requires-python` is not the version this is developed on.** It is the oldest
  interpreter a consumer can install it on. Raising it breaks those consumers;
  Renovate is configured not to touch it, and `install-floor` fails if it
  is raised past what that job installs.
- **`install-floor`'s `setup-python` pin must track that floor**, not
  `.python-version`. It carries a `python-version-ok` escape, so nothing here
  catches it drifting; `renovate.json` disables major/minor
  `actions/python-versions` bumps to every `setup-python` input after one took it
  from 3.11 to 3.15. Don't remove that rule.
- **The repo checks itself**, and `README.md` is deliberately not in
  `.python-version-ignore` — so the prose rules keep the documentation honest on
  the next bump. It *is* in `.node-version-ignore` and `.pnpm-version-ignore`,
  because with no `.nvmrc` or `packageManager` here its node/pnpm examples are
  all those toolchains would find. Don't resolve that asymmetry either way.
- **`src/version_drift/toolchains/*` is ignored by all five ignore files**,
  because a rule's own definition cites the tokens it matches. A new toolchain
  whose TOML cites another toolchain's token shape needs that ignore entry too.
- **Don't rename `ruff-linter.toml` to `ruff.toml`.** ruff reads that filename as
  its own config and fails on `unknown field \`components\``.
- **The inline escape is matched per line.** A comment above the offending line
  suppresses nothing.
- **Structured fields have no inline escape**, JSON having no comments; the only
  way out is the ignore file.
- **`--fix` refuses a pin written beside a content digest**, and refuses it per
  *match* rather than per rule: the same rule rewrites a bare tag on one line and
  declines the digest-pinned one below it. `Rule.fixable` is the rule-wide form
  of the same idea -- right for ruff's and djLint's revs, wrong for this.
- **`ruff-pin` and `djlint-pin` stay `fixable = false`.** Their source is the
  hook `rev:`, which autoupdate and Renovate move, so a mismatch is usually the
  rev lagging; `--fix` would downgrade the dependency instead.

## Versioning

Pick the number by what it does to a consumer's next commit rather than by the
size of the diff. The hook sweeps the whole repo on every commit, so a new rule
can fail a repo that passed yesterday even though nothing in that repo changed.
That is a minor, and the commit message has to say so.
