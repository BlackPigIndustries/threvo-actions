# Security policy

## Supported versions

The `0.1.x` release line receives security fixes. Development snapshots and
older `0.x` lines are unsupported. See `docs/versioning.md` for the distinction
between the supported Python API and experimental interoperability surfaces.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use GitHub's private
security-advisory reporting for the repository. Include affected revision,
impact, reproduction steps using synthetic data, and any suggested mitigation.
Do not include live credentials, payment data, or personal data.

Maintainers will acknowledge a report within three business days, coordinate a
fix and disclosure window, and credit reporters who want attribution. Do not
test against production systems or data without explicit authorization.

## Security boundary

The library cannot establish host authorization, canonical business state, or
external completion on its own. Applications must provide and enforce those
controls. See [docs/threat-model.md](docs/threat-model.md) for the initial model.
