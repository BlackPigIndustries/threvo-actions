# Gradual-reveal adoption record

Status: **awaiting an independent participant and workflow-built `0.1.4`
candidate**

Promotion gate: **pending**; release=v0.1.4; candidate_source=pending

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

The one top-level promotion marker is release-specific. Set it to exactly
`Promotion gate: **passed**; release=vX.Y.Z; candidate_source=<40-character
commit>` only when the ledger qualifies that release's exact workflow-built
candidate. Historical entries do not contain top-level promotion markers, and
the release workflow requires exactly one marker matching its release tag and
candidate source commit.

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
| Timing | ISO 8601 start/end; monotonic start/end; wall-clock, validated paused, and scored elapsed minutes; pause intervals and reasons |
| Assistance and failures | Complete facilitator/tool assistance and failure log |
| Acceptance | Command, output artifact, and all nine safety outcomes |
| Wiring | Eligible files/line spans, added LOC, exclusions, and expert/candidate delta |
| Result | Absolute gates, at least 30% wiring reduction, and pass/fail |

Scored elapsed minutes are the monotonic elapsed duration minus only the
facilitator-caused pauses allowed by the methodology's clock and pause rules.
The absolute gates use scored elapsed time: first action at most 60 minutes;
fifth action at most 30 minutes and at most 500 eligible host lines; all nine
production-safety conditions pass; and eligible definition/composition wiring
falls by at least 30% versus the expert baseline. Shared infrastructure and
marginal action cost are reported separately.

## Support review entries

The 120-day experimental support decision is a separate hash-linked entry in
this ledger. It must record:

| Field | Required value |
| --- | --- |
| Production consumer | Public identity or durable pseudonym, action type, deployment revision, pinned library version, and library source commit |
| Consumer equivalence | Baseline and gradual-reveal fingerprints, acceptance command and complete output, and pass/fail |
| Isolation | Transaction-coherence and tenant-isolation commands, complete outputs, and pass/fail |
| Rollback compatibility | Tested old/new runtime versions, both proposal directions, commands, complete outputs, and pass/fail |
| Post-adoption window | ISO 8601 start/end, action and outcome counts, verification retries, complete anomaly/failure log, and remediations |
| Decision | Support, revise, or retire; accountable owner; decision timestamp; and pass/fail for every required condition |

A support decision requires every named condition to pass and no unresolved
safety anomaly. Missing evidence results in revise or retire, never implicit
support. Apply the same integrity and correction rules as candidate entries.

## Stable promotion entries

Moving any experimental name into the supported package root requires another
hash-linked decision entry with:

| Field | Required value |
| --- | --- |
| Qualifying adoption | Either the second production action's durable identity, deployment revision, observation window, and outcomes, or an external design partner's identity or durable pseudonym and independence statement |
| Independent DX proof | Expert-baseline and passing candidate entry digests, with each absolute and comparative gate result |
| Post-adoption safety proof | Passing support-review entry digest and confirmation that its observation window has no unresolved safety anomaly |
| Promoted contract | Exact experimental names proposed for root export, compatibility commitment, migration note, accountable owner, and target release |
| Decision | Pass/fail for each prerequisite, decision timestamp, and promote, revise, or retain-experimental result |

The entry fails closed if any referenced digest is missing, any prerequisite is
failed, or either qualifying adoption path lacks its required identity and
observation evidence.

## Ledger entries

No scored entries exist. The coding-agent clean-room exercise remains formative
evidence only and is not copied into this ledger as a passing attempt.
