# Security policy

## Supported versions

The latest `0.6.x` patch release receives security fixes; pin it exactly.
Development snapshots and older `0.x` releases are unsupported. See
`docs/versioning.md` for the distinction between the supported Python API and
experimental interoperability surfaces.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's
[private vulnerability reporting](https://github.com/BlackPigIndustries/threvo-actions/security/advisories/new)
for the repository. Include affected revision,
impact, reproduction steps using synthetic data, and any suggested mitigation.
Do not include live credentials, payment data, or personal data.

Maintainers will acknowledge a report within three business days, coordinate a
fix and disclosure window, and credit reporters who want attribution. Do not
test against production systems or data without explicit authorization.

## Between a fix and its advisory

This repository is public, so every push discloses. While a reported
vulnerability has no published advisory, maintainers:

- re-read each commit, changelog entry, pull request and release note before
  pushing it, and what is already public, for pointers to the issue: a
  dependency advisory that names the same class, example inputs or encodings,
  or a sentence about what used to depend on what;
- describe the fix by its new behaviour, not by the attack it stops;
- publish the advisory as soon as the fixed release is on PyPI.

## Security boundary

The library cannot establish host authorization, canonical business state, or
external completion on its own. Applications must provide and enforce those
controls. See [docs/threat-model.md](docs/threat-model.md) for the initial model.
