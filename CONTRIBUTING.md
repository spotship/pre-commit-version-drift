# Contributing to pre-commit-version-drift

Thanks for taking the time. This is a small, deliberately conservative tool, and
the bar for a new rule is "it cannot produce a false positive", because a hook
that cries wolf gets `--no-verify`'d and then catches nothing at all.

## Project status

Pre-release, and **deliberately untagged**. Consumers pin a commit SHA via
`pre-commit autoupdate`, and re-running it is how they move forward — see
**Install** in the README. The engine's TOML contract (forms, rules, globs,
`field`, `source_field`) may still change.

Because there is no semver, a new rule reaches a consumer as "my unrelated
commit now fails pre-commit". That is the cost of this choice and it is on us to
soften: anything that makes an existing repo newly fail should say so plainly in
its commit message.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By
participating you agree to abide by it. Report unacceptable behaviour to
conduct@spot-ship.com.

## Commit messages and PR titles

Conventional Commits, present tense:

```
type(scope): short imperative summary
type: short imperative summary
```

Allowed types: `feat`, `fix`, `perf`, `refactor`, `style`, `test`, `ci`, `docs`,
`revert`, `build`, `chore`.

There is no automated release, so the type does not drive a version bump — it is
there so the history reads consistently and so a consumer can tell from the log
what changed between the SHA they had and the one they are moving to. Spot Ship
internal contributors should also prefix the summary with the
ClickUp ticket id (`feat(node): ENG-1234 add the pnpm toolchain`); external
contributors are not expected to include one.

## Developer Certificate of Origin (DCO)

All commits must be signed off:

```sh
git commit -s
```

This certifies you wrote the patch or have the right to submit it under the
project's licence. See [developercertificate.org](https://developercertificate.org/).
The DCO app lets you remediate your own unsigned commits.

## Local validation

```sh
task init         # pip install -e '.[dev]' + pre-commit install
task lint         # ruff check + ruff format --check
task test         # pytest
task check        # run this repo's own hook against itself
task try-repo     # drive the packaged hook the way a consumer gets it
```

Run all five before opening a PR. `task check` matters: the repo checks itself,
and `README.md` is deliberately not excluded, so documentation that names a
version has to stay correct.

## Adding a rule or a toolchain

A new language is a TOML file in `src/version_drift/toolchains/`, not a code
change. Nothing in `model.py`, `config.py`, `scan.py` or `cli.py` is
language-specific — please keep it that way, and if a rule seems to need an
engine change, say so in the PR so the shape can be discussed first.

For a rule, the PR should say:

- **What writes this pin**, and in which files. A rule for a shape nothing
  writes is not worth its false-positive surface.
- **Why it cannot false-positive.** Name what the pattern could otherwise hit:
  a package whose name contains the token, a version in prose, a compatibility
  matrix, the rule's own documentation.
- **Whether it is a pin or a floor.** A floor declares the oldest version
  supported, which is a different fact from the one the repo runs on, and only a
  floor *above* the pin is wrong.
- **What `--fix` would do to it.** If rewriting it could produce something
  valid-looking but wrong, it should not be fixable.

Tests drive the real CLI against a throwaway git repo — see `tests/conftest.py`.
Please include both a case that fires and a case that must stay silent.

## Pull request workflow

1. Fork and branch from `main`.
2. Make the change, with tests.
3. Run the five commands above.
4. Open the PR; fill in the template.

CI runs pre-commit, the test matrix, the packaged-hook jobs and the
`requires-python` floor check. All must pass.

## Reporting bugs and requesting features

Use the issue templates. For a false positive or a missed pin, the most useful
report is the smallest file content that reproduces it plus the contents of the
source-of-truth file.

## Security

Do not report vulnerabilities in public issues — see [SECURITY.md](SECURITY.md).

## Licence

By contributing you agree that your contributions are licensed under the MIT
Licence, as in [LICENSE](LICENSE).
