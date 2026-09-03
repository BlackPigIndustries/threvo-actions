# Gradual-reveal adoption record

Status: **awaiting an independent participant and workflow-built `0.1.4`
candidate**

Promotion gate: **pending**

This is the append-only evidence ledger for the expert baseline and every
gradual-reveal candidate attempt. A local source run, maintainer trial, or AI
agent run cannot pass the independent-human gate.

## Integrity rules

Each entry records its canonical JSON SHA-256 and the previous entry digest.
Canonicalize the entry object with RFC 8785 JSON Canonicalization Scheme (JCS),
encode the resulting JSON as UTF-8 without a byte-order mark or trailing
newline, and record the lowercase SHA-256 hex digest of those bytes. The
canonical object includes `previous_entry_digest` and every evidence field but
excludes `entry_digest` itself. Evidence values must not contain JSON
floating-point numbers. The first entry uses `genesis`. Never edit or replace
an earlier entry. A correction is a new entry that identifies the superseded
digest. The candidate source commit, wheel SHA-256, source-distribution
SHA-256, fixture commit, methodology revision, and acceptance-command output
must remain recoverable.

A failed or assisted attempt stays visible. Remediation requires a new source
commit, a rebuilt wheel, and a new independent participant. Timestamps are
timezone-aware and include both wall-clock and monotonic elapsed time.

## Expert baseline

Pending. Run the production-shaped fixture against the expert
`ActionDefinition`/`ActionRuntime` path before revealing the candidate API.
Record all nine safety outcomes, eligible definition/composition wiring,
excluded lines, shared infrastructure, first-action time, and fifth-action
marginal time under the same protocol used for candidates.

## Candidate attempts

Pending. Every attempt must record:

| Field | Required value |
| --- | --- |
| Entry digest / previous entry digest | SHA-256 / `genesis` or prior digest |
| Participant | Identity or durable pseudonym plus independent participant statement |
| Relevant experience and prior exposure | Free text |
| Source commit | Exact 40-character commit |
| Candidate workflow | Exact GitHub Actions candidate run ID and run URL |
| Candidate artifacts | Workflow-built wheel filename and SHA-256; workflow-built source-distribution filename and SHA-256 |
| Fixture and methodology | Exact commits/revisions |
| Start, end, pauses | ISO 8601 timestamps, monotonic elapsed, validated pause log |
| Assistance and failures | Complete facilitator/tool assistance and failure log |
| Acceptance | Command, output artifact, and all nine safety outcomes |
| Wiring | Eligible files/line spans, added LOC, exclusions, and expert/candidate delta |
| Result | Absolute gates, at least 30% wiring reduction, and pass/fail |

The absolute gates are: first action at most 60 minutes; fifth action at most
30 minutes and at most 500 eligible host lines; all nine production-safety
conditions pass; and eligible definition/composition wiring falls by at least
30% versus the expert baseline. Shared infrastructure and marginal action cost
are reported separately.

## Ledger entries

No scored entries exist. The coding-agent clean-room exercise remains formative
evidence only and is not copied into this ledger as a passing attempt.
