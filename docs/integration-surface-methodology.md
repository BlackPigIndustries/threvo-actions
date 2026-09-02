# Integration-surface measurement methodology

This protocol measures whether a team can integrate one confirm-first action
within 60 minutes and add a fifth action within 30 minutes. It measures the
library's application-facing surface, not the time required to invent payment
policy, build a production connector, or obtain regulatory approval.

No passing independent-human timing evidence exists yet. A
[coding-agent clean-room exercise](testing/clean-room-adoption-2026-08-30.md)
passed the task-specific 60-minute and 30-minute targets, but it used an
in-memory fixture and does not satisfy this broader external-adoption protocol.
The criteria below remain pending until a participant who did not implement
`threvo-actions` completes them and the raw worksheet is published.

## Exercise fixture and participant

Use one independent participant who is competent in Python and the host
framework but has not contributed code to this repository or previously
integrated this library. Record their relevant experience and any prior
exposure to the documentation.

The facilitator supplies a small, runnable host application with:

- an authenticated tenant/request context and a server-side authority service;
- existing canonical business state and one already working mutation per
  exercise brief;
- a target simulator or sandbox that supports stable effect identity,
  idempotent submission, ambiguous acceptance, and query by that identity;
- a clean baseline commit and focused acceptance tests that initially fail; and
- cached dependencies, documented test commands, and no hidden credentials.

The participant receives the package, public documentation, API reference,
fixture application, and action brief. They may inspect library source as a
normal open-source user would. They do not receive a completed action adapter,
private coaching, or copies of the reference application's implementation.

Environment setup is completed before timing: supported Python is installed,
the fixture checks out and its unrelated tests pass, required services start,
and dependency downloads are warm. Time spent learning the API, reading docs,
implementing, testing, and debugging the integration is included.

## First-action exercise: 60 minutes

Start the timer when the facilitator reveals the first action brief and gives
the participant control of the clean baseline checkout. Stop it only when the
participant declares completion and the facilitator runs the acceptance
command successfully on the participant's working tree.

The first action is complete only when the tests demonstrate:

1. typed command, private snapshot, preview, and safe-result models;
2. preparation from live canonical host state;
3. no executor call without server-recorded, bound authority;
4. live authorization and material-drift refusal immediately before admission;
5. a stable semantic-effect identity and an atomic host mutation precondition;
6. governed execution using the stored private snapshot rather than resumed
   client or model arguments;
7. an ambiguous target response entering verification instead of blind resend;
8. authoritative query reaching a terminal result; and
9. a repeated runtime request returning the existing outcome without another
   target effect.

The scored limit is 60 minutes.

## Fifth-action exercise: 30 minutes

Before this exercise, four distinct actions must already pass the same core
acceptance shape in the host application. They may share deliberately reusable
application infrastructure such as stores, identity adapters, cryptographic
providers, authority services, scheduling, and test helpers.

Start the timer when the fifth action brief is revealed on a clean branch from
the accepted four-action baseline. Stop it when the fifth action's acceptance
tests pass under facilitator execution. The fifth action must define a new
action type and business mutation; renaming or cloning an existing action with
no new domain rule does not qualify.

Apply the same nine completion conditions as the first-action exercise. Record
which ports were reused unchanged, configured, extended, or implemented anew.
The scored limit is 30 minutes.

## Clock and pause rules

Record start and end as timezone-aware ISO 8601 timestamps and also use a
monotonic stopwatch. Report:

- wall-clock elapsed minutes;
- each pause with start, end, duration, and reason; and
- scored elapsed minutes: wall-clock time minus validated facilitator-caused
  pauses.

Only failures outside the participant's control may pause the score, such as a
fixture defect, power/network outage, or unavailable package registry after
dependencies were verified. Documentation lookup, design decisions, coding,
dependency selection, ordinary test execution, participant mistakes, and
debugging are not pauses. Both wall-clock and scored elapsed time remain in the
published worksheet.

## Required port inventory

For each action, inventory every boundary below. Mark it `library supplied`,
`reused unchanged`, `configured`, `extended`, `new host code`, or `not used`,
and link the implementing file and symbol.

| Boundary | Required host decision |
| --- | --- |
| Command, private snapshot, preview, and result models | Which fields cross each trust boundary? |
| Preparation port | How is canonical state resolved and the semantic effect named? |
| Authorization port | Who may prepare, decide, execute, read evidence, and under which tenant? |
| Authority evaluator | What evidence and distinct principals satisfy the action? |
| State resolver | Which live changes are material and how is a replacement prepared? |
| Governed executor | What atomic precondition protects the host mutation? |
| Verifier | Which target query proves completion, failure, provisional absence, or final absence? |
| Commitment provider | Which keyed construction, key handle, and rotation policy are used? |
| Protection codec | How is the private snapshot protected and cryptographically erased? |
| Retention port/store | Who may erase, and what remains in the tombstone and backups? |
| Action store | In-memory for the exercise or PostgreSQL; how are migrations and roles supplied? |
| Identity/framework adapter | How do trusted tenant and participant identities enter the runtime? |
| Reconciliation scheduling | What causes verification to run after it becomes due? |
| Event sink | Where do minimized runtime events go, if configured? |

If the fixture supplies a deliberately fake security provider, label it as
such. A fast exercise with plaintext or deterministic test cryptography is not
evidence of production readiness.

## Host-supplied non-test LOC

Measure the application code needed to integrate the library, not the library
or fixture business operation itself. At each baseline and completion commit,
run:

```bash
git diff --numstat <baseline>...<completion> -- <eligible-host-source-paths>
```

Record the added-line column for every eligible text file and its
classification. Count added physical lines, including comments and docstrings,
because they are maintained integration surface. Record deletions separately;
do not subtract them from the added-line total.

Include host-owned, non-test Python source added or changed to define models,
ports, action registration, identity bridging, authority bridging,
reconciliation wiring, and integration-specific configuration code.

Exclude:

- `tests/`, fixtures, snapshots, golden files, and benchmark code;
- `threvo_actions` package source and copied reference-example code;
- generated files, lockfiles, vendored code, migrations, deployment manifests,
  and formatter-only churn;
- pre-existing fixture business services, policies, target simulator, and
  framework bootstrap; and
- generic production capabilities built outside the timed exercise.

If a file mixes eligible integration code with excluded code, list the exact
eligible line spans and count them manually. Publish both the eligible total
and all excluded additions so that a low number cannot hide moved complexity.

## Worksheet

Publish one worksheet per attempt with:

| Field | Value |
| --- | --- |
| Exercise | first action / fifth action |
| Participant and independence statement | |
| Python, OS, database, framework, and package versions | |
| Baseline and completion commit | |
| Action type and target simulator/sandbox | |
| Start and end timestamps | |
| Wall-clock, paused, and scored minutes | |
| Pause log | |
| Acceptance command and result | |
| Required-port inventory | |
| Reused/configured/extended/new port counts | |
| Eligible host non-test added LOC | |
| Eligible file-by-file LOC | |
| Excluded additions and reason | |
| Facilitator assistance | |
| Participant blockers and documentation gaps | |
| Pass/fail against the time limit | |

For the gradual-reveal evaluation, record the expert baseline first under this
same fixture and acceptance command. Candidate attempts must use a wheel built
by the release workflow, not an editable checkout. Add the candidate source
commit, wheel and source-distribution SHA-256 digests, eligible expert wiring,
eligible candidate wiring, absolute LOC delta, and percentage reduction.

Entries are append-only and hash linked: canonicalize each completed worksheet
as JSON, record its SHA-256, and include the previous entry digest (`genesis`
for the first entry). Never replace a failed or assisted attempt. Any source,
fixture, documentation, acceptance-test, or methodology change creates a new
entry and may require a new independent participant.

A passing candidate must satisfy the time limits, all nine safety conditions,
fifth-action eligible host code of at most 500 lines, and at least 30% less
eligible definition/composition wiring than the expert baseline. Report shared
first-integration infrastructure separately from marginal additional-action
code; moved safety code remains eligible rather than disappearing from the
count.

Retain the failing worksheet as well as successful attempts. Changing the
fixture, documentation, acceptance tests, or scoring rules creates a new
methodology revision and must not be silently compared with earlier results.

Download counts, maintainers repeating their own examples, and an AI agent
generating an adapter do not satisfy this integration-time evidence or the
separate external-adoption gate.
