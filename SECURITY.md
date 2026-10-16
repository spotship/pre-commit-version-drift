# Security Policy

## Supported versions

| Version           | Supported         |
| ----------------- | ----------------- |
| Unreleased `main` | Yes               |
| Tagged releases   | Not yet available |

No release has been published yet. Until the first tag, only the default branch
(`main`) is in scope for security review.

## Threat model

This is a pre-commit hook. It runs on a developer's machine and in CI, with the
privileges of whoever invoked `git commit`, and it:

- reads every git-tracked file in the repo it is checking;
- with `--fix`, **writes** to files it has matched;
- loads toolchain definitions, including arbitrary regular expressions, from
  `.version-drift.toml` in the repo being checked.

It makes no network requests and reads no credentials. The consequences worth
reporting are therefore: a path traversal or write outside the repo, a
regular-expression denial of service reachable from repo content, arbitrary code
execution from a crafted `.version-drift.toml`, or `--fix` corrupting a file in a
way the documented hazards do not already cover (see **Autofixing** in the
README).

Note that a repo's own `.version-drift.toml` is trusted by design: anyone who can
edit it can already edit the `.pre-commit-config.yaml` that invokes the hook.

## Reporting a vulnerability

**Do not open a public GitHub issue for suspected vulnerabilities.**

Preferred path:

1. Use [GitHub Private Vulnerability Reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
   on this repository, if enabled.
2. Otherwise, email `security@spot-ship.com` with the subject prefix
   `[pre-commit-version-drift security]`.

Please include:

- Affected commit, tag, or branch.
- A minimal reproduction: the repo contents or `.version-drift.toml` that
  triggers it, and the command run.
- Your assessment of the impact.
- Any suggested mitigation or patch.

## Disclosure window

- Acknowledgement: within **5 business days** of receipt.
- Initial assessment: within **15 business days** of receipt.
- Fix availability and coordinated disclosure: depends on severity,
  exploitability, and release readiness. We will keep reporters informed
  throughout.

We follow responsible disclosure practice and credit reporters in release notes
unless asked to remain anonymous.
