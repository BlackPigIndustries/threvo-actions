---
name: threvo-actions
description: Build or integrate accountable financial actions with the threvo-actions Python library. Use when code imports threvo_actions, when adding confirm-first agent or application writes, or when a financial mutation needs bound authority, drift refusal, semantic idempotency, receipts, or authoritative completion verification.
license: Apache-2.0
metadata:
  author: Threvo
  version: "0.1.4"
---

# Threvo Actions

Use `threvo-actions` to coordinate a high-consequence mutation while the host
application retains business truth, authorization, execution, key custody, and
authoritative verification. The library is a control runtime, not a payment
rail, policy engine, or source of financial state.

## Start from the installed contract

Before writing code, inspect the version pinned by the application and prefer
its local package source, type signatures, and documentation over remembered
APIs. When working in the library checkout, the runnable source of truth is
`examples/docs/quickstart.py`; the public contract is re-exported from
`threvo_actions`. From an installed distribution, `threvo-actions skill path`
prints this skill's directory so a standard skill installer can consume it.

Do not copy an example until these four host seams are identifiable:

1. the canonical reader that resolves current business state;
2. the live authorization service for preparation, decision, execution, and read;
3. the governed mutation with an atomic precondition and target idempotency; and
4. the authoritative query that proves the business effect completed.

If any seam is missing, surface it as an integration gap. Do not replace it
with model output, conversation history, a framework approval flag, or a
successful HTTP response.

## Choose the authoring surface

- Prefer the experimental `ActionApplication` plus a strict `ActionSpec` for a
  new integration. Register a typed `ActionRecipe` explicitly, freeze the
  catalog, and bind a dependency container from a fresh host operation scope.
  Durable services may be shared references inside that container. This
  gradual-reveal surface compiles to the same expert runtime and cannot grant
  policy.
- Treat `ActionApplication.bind()` as trusted host composition. Recipe,
  definition, and runtime-construction failures preserve their original
  exception and traceback for the author. Catch them at the host API or agent
  boundary, log them under host policy, and return a stable content-safe host
  error; never forward arbitrary exception text, tracebacks, causes, or locals
  to an untrusted caller or model.
- Use `Action[Command, Snapshot, Preview, Result]` when one host object already
  owns every action port. Build `ActionDefinition` directly when the host owns
  the ports as separate expert-level adapters. Do not introduce a second
  lifecycle.
- Call `application.inspect(handle)` for static, allowlisted configuration
  inspection. It reports closed boundary roles and invariants, not model class
  names; it does not contact stores, run recipes, or report readiness.
- Use `effect_kind="single"` for one indivisible effect. Use `"itemized"` only
  when the target and verifier can identify every item outcome.

Read [references/authoring.md](references/authoring.md) when creating or
changing an action contract.

## Implement the lifecycle

1. Define four strict, frozen Pydantic models. Treat the command as untrusted
   intent, the private snapshot as canonical execution state, the preview as
   minimized display data, and the result as safe output.
2. In `prepare`, resolve trusted state and create a stable host-defined
   `semantic_effect_reference`. Never put secrets or unnecessary PII in the
   preview, result, receipts, errors, or telemetry.
3. Implement live `can_prepare`, `can_decide`, `can_execute`, and `can_read`
   checks. Approval requirements count evidence; they never grant permission.
4. In `resolve`, read current state again and return an atomic
   `execution_precondition`. Material drift must refuse execution and normally
   produce a fresh proposal rather than silently widening the approved effect.
5. In `execute`, pass the precondition into the transaction or target request.
   Distinguish accepted, known failure, unknown failure, stale-no-effect, and
   itemized partial success honestly.
6. In `verify`, query the authoritative target. Transport acceptance is not
   completion. Reconcile while `result.needs_reconciliation` is true.
7. Keep erasure default-deny unless the host has a real privileged retention
   authorization path.

Read [references/host-integration.md](references/host-integration.md) when
migrating an existing mutation or adding durable persistence.

For persistence, use PostgreSQL or MySQL 8 when production-oriented multi-worker
operation and database-role separation are required. MySQL uses explicit
packaged migrations, InnoDB transactions, and separate security-definer runtime
and retention procedures; MariaDB is not supported. SQLite is official only for
local, evaluation, test, and bounded single-writer use. A custom store owns its schema
and migrations and must preserve atomic compare-and-set, semantic-effect
admission, tenant isolation, append-only evidence, and resumable logical erasure. Run the
shared conformance suite plus database-native race and rollback tests.

For PostgreSQL deployment, keep the library migration ledger separate from a
host Alembic ledger. Run `threvo-actions postgres migrate` before `alembic
upgrade` in a serialized deployment job; never invoke the installed package
migrator dynamically from Alembic's `env.py`. Use `postgres script --all` or
`--from-version VERSION` when an immutable, complete SQL artifact is required.
The three action roles may target a dedicated database for maximum isolation.

SQLite erasure is a logical tombstone, not proof of secure deletion from free
pages, journals, WAL files, snapshots, backups, or the filesystem. Use
revocable encryption keys and explicit deletion policies for those copies.

## Establish authority safely

Use `SingleApproval`, `AnyApproval`, or `MOfNApprovals` only for evidence
sufficiency. Evidence must be created after the host authenticates and
authorizes the confirming authority, and must bind the tenant, action type,
proposal, semantic effect, commitment, audience, channel assurance, issue
time, and expiry.

Always repeat live execution authorization. Expired evidence remains useful for
audit but cannot satisfy the current requirement. Do not treat a Pydantic AI
`ToolApproved`, client boolean, signed-in user message, or replayed chat history
as `AuthorityEvidence`.

## Add an agent framework only at the edge

The core action must run without an agent framework. For Pydantic AI, use the
optional `ActionCapability`. Prefer `ScopedActionToolBinding` so every prepare
or deferred resume enters a fresh host-owned dependency scope, then build
`ActionAgentContext` from those authenticated dependencies, never tool
arguments. The fixed-runtime binding remains available for already composed
expert integrations. Read
[references/pydantic-ai.md](references/pydantic-ai.md) for the current wiring
and deferred-resume trust boundary.

## Verify the integration

Use deterministic helpers only in tests. `EphemeralProtection` deliberately
loses data on restart and is never production encryption or key custody.

At minimum prove:

- unauthorized preparation, decision, execution, and read are denied;
- forged, expired, wrong-audience, and wrong-proposal evidence cannot execute;
- material drift and an atomic precondition race produce no effect;
- competing proposals cannot admit the same semantic effect;
- accepted or failed-unknown execution reconciles instead of blind retrying;
- only authoritative verification produces `verified`;
- previews, receipts, errors, and events contain no seeded sensitive values; and
- crash recovery does not steal an execution before its recovery lease expires.

Read [references/testing.md](references/testing.md) for helpers, conformance
checks, and the failure matrix.

## Non-negotiable boundaries

- Use `Decimal`, explicit currency, and timezone-aware datetimes. Never use
  `float` in a private snapshot.
- Boundary models configure `extra="forbid"`, `strict=True`, and `frozen=True`.
- Keep tenant and authenticated-user scoping in every host query.
- Persist protected snapshots separately from minimized previews.
- Preserve the `runtime_revision` on runtime-generated receipts. It identifies
  an exact release or source commit; never replace it with `0.0.0`, the host
  application version, or the version currently installed while reading an
  older receipt.
- Never claim exactly-once execution; combine semantic admission, target-side
  idempotency, and authoritative verification.
- Never claim verified completion from an executor receipt alone.
- Never log credentials, raw private snapshots, internal database identifiers,
  or unnecessary personal data through generic library surfaces.
