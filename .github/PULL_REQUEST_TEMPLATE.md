<!--
PR title MUST follow Conventional Commits:

    type(scope): short imperative summary
    type: short imperative summary

Allowed types: feat, fix, perf, refactor, style, test, ci, docs,
revert, build, chore. Use the present tense.

Spot Ship internal contributors should also prefix the summary with the
ClickUp ticket id, e.g. `feat(node): ENG-1234 add the pnpm toolchain`.
External contributors are not expected to include a ticket id.
-->

## Summary

<!-- What changed and why. 1–3 short paragraphs. -->

## Verification

```sh
task lint
task test
task check
task try-repo
```

<!-- Paste the relevant output, or summarize the result. If a command
     was skipped, explain why. -->

## Checklist

- [ ] PR title follows the Conventional Commits format above.
- [ ] All commits are signed off (DCO — `git commit -s`).
- [ ] Tests cover both a case that fires and a case that must stay silent.
- [ ] No engine change was needed for a new rule or toolchain — or the PR
      explains why one was.

<!-- Delete the section below if this PR adds no rule. -->

## New rule

- **Written by:** <!-- what tool or convention produces this pin, in which files -->
- **Cannot false-positive because:** <!-- what the pattern could otherwise hit -->
- **Pin or floor:** <!-- a floor is only wrong when it sits above the pin -->
- **`--fix` behaviour:** <!-- fixable, or why rewriting it would be unsafe -->
