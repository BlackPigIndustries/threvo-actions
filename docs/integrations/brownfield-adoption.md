# Brownfield adoption

The first full integration of `threvo-actions` was into Threvo, the application
from which the lifecycle was originally extracted. That pilot exposed a useful
boundary for applications that already have durable requests, approval screens,
workers and database transactions.

Start by keeping those application concepts. Bind one existing operation to the
runtime and replace only the lifecycle mechanics that are unsafe to duplicate.

## What the library can own

| Existing application need | Library surface |
| --- | --- |
| A durable request already has an identifier | Pass it as `proposal_reference` to `prepare()` |
| Product code needs a smaller lifecycle vocabulary | Use `classify_lifecycle()` and `LifecycleCategory` |
| An operator may reopen exhausted verification | Configure `RecoveryAuthorizationPort`; call `resume_verification()` only with an authenticated `RecoveryContext` |
| Periodic work needs bounded leases | Compose `RecoveryWorker` with the application's due-work source and action resolver |
| Existing Pydantic AI tools already route through confirmation | Validate them with `ExistingToolActionBinding` and `build_existing_tool_action_toolset()` |
| A deployment keeps a versioned master key in its secret system | Use `LocalKekEnvelopeProtection` with a host-owned wrapped-key store |
| Internal state must change atomically with lifecycle state | Use a caller-owned transaction source and run both writes on the same connection |
| A provider call must survive an application rollback | Commit lifecycle admission independently before the remote call |

The Stripe PostgreSQL exercise is installed as
`PostgresStripeHostExerciseAdapter`. Run it against the migrated ledger and an
ordinary writer path; importing a checklist or returning declared outcomes is
not conformance evidence.

`proposal_reference` is a host-owned identity, not an idempotency key. A second
`prepare()` call with the same reference does not replay the earlier result:
preparation may have read different business state, generated new protected
material, or produced another receipt identity. Read the existing proposal and
continue its lifecycle, or handle `ProposalAlreadyExistsError`; do not retry
preparation blindly.

## What remains application-owned

The application still authenticates people and agents, reconstructs current
roles, decides business permission, owns its confirmation and domain records,
and maps safe library results into product copy. It also owns transaction
boundaries, worker scheduling, webhook verification and provider credentials.

Webhook events should remain hints. Verify and deduplicate them at the ingress,
look up tenant-scoped candidate proposals, then call the normal reconciliation
path. Do not pass the provider payload into authority evidence or treat an event
as proof that the effect completed. Candidate lookup and principal
reconstruction depend on the host's schema and identity system, so the pilot
keeps that orchestration in Threvo.

## A progressive path

1. Bind one existing tool or service operation and keep the current approval UI.
2. Use the application's request identifier as the proposal reference.
3. Run the store and runtime conformance contracts against the real adapter.
4. Replace copied lifecycle status tables with the public classifier.
5. Add recovery reads and operator authorization before exposing recovery
   controls.
6. Run provider-specific host exercises and failure scenarios with independent
   database connections.
7. Adopt evidence export only after the host has reviewed its explicit
   omissions and unsigned-host-projection boundary.

This sequence follows the same progressive reveal as the authoring API: a host
can begin with a small binding and replace ports only when its storage,
authorization or operational needs require them.

Threvo is a maintainer-owned pilot. Its passing integration tests demonstrate
that the surfaces can work together, but they do not establish independent
adoption or production qualification for another application.
