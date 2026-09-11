# Stripe Actions: proposition and implementation pipeline

Status: implemented and locally validated; included in the 0.3.0 release candidate. This extends the existing
Stripe refund connector; it does not qualify new payment flows or release a version.

## Proposition

Give Python application and agent developers a straightforward refund interface
that incorporates reviewed payment binding, explicit currency limits, live host
authorization, durable intent reservation, ambiguous-outcome recovery and verified
completion. The ordinary API path should preserve these standards. Success means
correct behavior and comprehensible integration responsibilities, rather than a
claimed number of hours saved.

Initial customers are direct merchants building support/refund assistants and
internal operations tools. Hosts with existing identity, order/payment storage,
key custody and a worker are the first audience. Provider-specific authority and
business rules remain host-owned. This is a Python SDK integration, not a Stripe
Dashboard Marketplace app or a new payment protocol.

## Public interface and naming

Use `from threvo_actions.integrations.stripe import StripeActions` and
`actions.refunds.prepare(...)`. Use the name `StripeActions` to distinguish the
governed interface from `stripe.StripeClient`. `StripeRefundConnector` continues
to name the lower-level submit/verify boundary and `StripeSDKGateway` remains the
SDK transport adapter. Existing imports remain compatible.

- `RefundRequest`: untrusted business intent with payment reference and Money.
- `RefundPayment`: host-resolved payment/account, currency exponent and revision.
- `RefundSnapshot`: private binding of payment, intent, observed balance and policy.
- `RefundPreview`: minimized business reference and amount, never Stripe charge IDs.
- `RefundPolicy`: strict per-currency ceilings and live-mode opt-in; no inferred FX.
- `RefundRepository`: host payment/intent/reservation persistence protocol.
- `RefundHost`: constructor-injected repository and existing AuthorizationPort.
- `StripeRefundSettings`: runtime identity and lifecycle configuration.
- `StripeRefunds`: the refund operation group backed by one ActionDefinition.

Keep generic runtime names Action, AuthorityEvidence, Proposal, ExecutionResult
and VerificationResult. Use category-neutral language in shared agent instructions.
Do not rename persisted `finance.action/v1`, receipt vocabulary or established
imports as a cosmetic cleanup. An eventual wire rename needs a migration decision.
Payment references allow invoices, orders and other host concepts without forcing
an order schema onto the library. No generic connector registry is needed yet.

## Architecture

Convert the Stripe integration module into a package with compatible exports.
Keep transport/observations in gateway.py; put new models and policy in models.py;
host protocols in ports.py; composition and lifecycle ports in actions.py. All
dependencies point inward into the existing runtime. The root package continues
to import neither Stripe nor Pydantic AI. No Threvo backend, SQL or web framework
imports enter the connector.

The facade builds ActionDefinition and ActionRuntime. It owns preparation,
provider-state drift checks, policy binding, submission and verification. Hosts
implement the existing four authorization checks and a narrow repository protocol.
Reservation must atomically compare the current payment and intent, coordinate
other writers, check the supplied deadline, and exclude unresolved submissions.
The runtime store owns proposal/evidence state; it cannot substitute for host
business reservation. Hosts must durably discover due execution and reconciliation.

RefundPolicy uses frozen, strict Pydantic models, validated defaults and closed
outcomes. Duplicate currencies and nonpositive limits fail at construction.
Serialized policy changes invalidate existing snapshots even if a host forgets to
change a revision label. Model-native and JSON configuration share validation.
YAML loading is deferred until a real configuration-file consumer requires it.
Services remain typed Python objects, never executable configuration.

## Supported scope and invariants

Support exact positive partial/full refunds on paid, captured, undisputed direct
charges. Reuse existing account scoping and rejection of indirect Connect flows.
Live mode requires explicit policy opt-in and a separately qualified host. No
payment method, charge identifier, tenant identity or approver comes from agent
tool arguments. A stable semantic effect binds tenant + business intent; changing
amount, payment, account or policy cannot reopen a reserved submission.

Authority evidence remains created by authenticated host code. Framework approval
never creates authority. Check policy and host authority again at dispatch. Bound
submission by proposal, evidence and execution-lease expiry. A reserved/ambiguous
attempt proceeds only to independent verification. Empty lookup never permits
resend. Observations must match the original tenant and semantic intent before
provider verification. Retention remains default-deny.

Existing Stripe reference-app persisted shapes and action identities remain
unchanged. Add a separate runnable facade example with explicit process-local test
adapters; retain the PostgreSQL reference app as the durable hosting example.
Migration of its persisted proposals requires a separate qualification step.

## Implementation units, in order

1. Record this proposition, API shape, limits and naming decisions.
2. Add failing behavior tests for policy validation, authority refusal, payment
   scoping, policy drift, reservation, unknown outcomes and verified completion.
3. Implement compatible package structure, typed contracts and the runtime-backed
   facade; preserve connector replay and binding behavior.
4. Add a complete deterministic example and a Pydantic AI binding recipe. Keep
   credentials and authority outside model arguments. Validate typing and imports.
5. Update README, integration/reference docs, target-customer proposition,
   changelog and bundled agent skill. Use uv installation instructions.
6. Run focused lifecycle/connector/agent regressions, Ruff, strict mypy, Bandit,
   documentation build and package build. Record observed results below.

## Follow-on pipeline

| Stage | Deliverable | Exit condition |
| --- | --- | --- |
| Host qualification | Durable facade host using real identity, atomic reservations, key custody, due-work discovery and case handling | Restart, concurrent intent, authority revocation and ambiguous-provider scenarios pass |
| Reference-app adoption | Explicit mapping/migration for old snapshots and action versions | Pending 0.2.0 proposals remain readable/reconcilable and cannot be replayed |
| Agent/human DX validation | Independent integration and scenario evaluation | Users choose correct actions, explain refusals and distinguish pending from complete without source archaeology |
| Provider compatibility | Pinned SDK/API fixtures and direct-Connect sandbox evidence | Account scope, revoked credentials and webhook/recovery cases pass |
| Next Stripe action | Select subscription cancellation, invoice voiding or another requested operation | Its own preconditions, idempotency and authoritative outcome contract is reviewed; no generic SDK passthrough |
| CRM proof | One governed company/contact change | Demonstrates reusable runtime and neutral vocabulary without finance-specific core additions |
| Ontology experiment | Small object/relationship/action contract over existing services | Useful in two domains before independent package extraction |

No production credentials, push, merge or release are required by this iteration.
Keep work on develop as requested.

## References

- [Pydantic configuration](https://docs.pydantic.dev/latest/concepts/config/): inherited model configuration and strict boundaries.
- [Stripe refund creation](https://docs.stripe.com/api/refunds/create): charge refund API semantics.
- [Stripe idempotency](https://docs.stripe.com/api/idempotent_requests): finite retention; host reservations remain necessary.

## Verification record

- Added 19 facade tests, including lost provider responses, live-mode and currency
  policy, changed payment/policy/balance, revoked authority during reservation,
  expiry before dispatch, uncertain reservation acknowledgement and agent tool scope.
- Focused Stripe, Pydantic AI, action/application, runtime, registry,
  canonicalization and architecture regressions: **201 passed, 20 skipped**.
  The skipped Stripe reference-app cases require a dedicated PostgreSQL database.
  A local server attempt could not start because only libpq client utilities are
  installed; Docker is not running. Database-native compatibility remains a gate.
- Ruff lint/format, strict mypy across source/examples/benchmarks, Bandit,
  dependency lock consistency, strict documentation build and wheel/sdist build
  passed. No dependency version changes or credentials were needed.
- Runnable facade example: prepared → authorized → failed_unknown → verified,
  with one simulated provider submission.
- Existing public Stripe connector tests pass after the package split. The
  production host and real-provider qualification stages above remain open.
