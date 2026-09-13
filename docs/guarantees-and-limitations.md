# Guarantees and limitations

`threvo-actions` coordinates a confirm-first action lifecycle. It does not
replace the application that knows whether an action is allowed or the system
that knows whether the financial effect occurred. Every guarantee below is
therefore assigned to the component that actually supplies it.

The documented Python API and CLI are supported at the exact `0.5.0` release;
upgrading requires the [documented migration review](releases/0.5.0.md).
Serialized interoperability forms remain experimental. These are implementation
guarantees for a tested release, not a promise that the host application or an
external financial system implemented its side correctly. See
[Versioning](versioning.md).

## Responsibility matrix

| Guarantee | Supplied by | Conditions and boundary |
| --- | --- | --- |
| Strict typed boundaries | Library runtime | Public boundary models are frozen Pydantic v2 models that forbid extra fields and coercion. This validates shape, not business truth. |
| Deterministic canonical payloads | Library runtime | The internal canonical JSON profile normalizes Unicode, sorts object keys, and rejects floats. It is not a public canonicalization standard. |
| Proposal-scoped commitment binding | Library runtime + store/provider | The runtime domain-separates the canonical private snapshot and binds its commitment to the proposal. Security depends on a host provider using a suitable keyed construction and protecting its keys. |
| Private snapshot and display-preview separation | Library runtime + host application | The runtime persists the private snapshot only through the configured protection codec and returns the separately supplied preview. The host must keep sensitive fields out of the preview and safe result models. |
| Bound, expiring authority evidence | Library runtime + host application | The runtime checks tenant, action version, proposal, semantic effect, commitment, audience, assurance, issue time, expiry, and authenticated authority identity. The host authenticates the authority and decides whether the accumulated evidence satisfies policy. |
| Live authorization before effect admission | Host application, invoked by the library runtime | The runtime invokes the host authorization ports again before execution and does not call the executor when they refuse. A permissive or incorrect host policy remains permissive or incorrect. |
| Live state re-resolution and drift refusal | Host application, coordinated by the library runtime | The host resolves current canonical state and declares material drift. The executor must also enforce the returned precondition atomically and may report `stale_no_effect` if that final guard loses. The runtime makes either stale path terminal and may persist a freshly prepared replacement. It cannot infer which business fields are material. |
| Guarded lifecycle transitions | Store/provider | The in-memory, PostgreSQL, MySQL 8, and bounded-use SQLite stores enforce tenant-scoped revision checks and allowed transitions. A custom store earns this claim only after passing the store conformance contract and database-native concurrency tests. |
| Store security profile | Store adapter + deployment | Official profiles expose tested topology, privilege separation, per-guarantee enforcement level, and explicit data-handling exclusions. They distinguish database-enforced, adapter-process, and unsupported guarantees; they do not configure encryption, authenticate evidence issuers, erase external copies, or certify the deployment. |
| Single admission for a semantic effect | Store/provider | The store atomically admits at most one proposal for a host-defined `(tenant, action type, semantic effect)` identity. This prevents competing runtime proposals from being admitted; it does not make a remote financial mutation exactly once. |
| No blind resend after an ambiguous outcome | Library runtime + host application | Failed-unknown and accepted outcomes enter verification. Resend becomes eligible only after connector-declared authoritative final absence, its settling boundary, target idempotency, and an action policy that permits resend. The host still owns resend policy and the stable target identity. |
| Authoritative completion status | Authoritative external target, projected by the host verifier | `verified` is reachable only from the verifier's terminal completion result. The claim is only as strong as the target queried and the verifier's implementation; transport acceptance and model prose are never completion proof. |
| Bounded verification attempts | Library runtime + store/provider | Verification attempts are lease-admitted and bounded. Exhaustion becomes `verification_unresolved`; it is not converted into success or safe resend. The host must schedule later reconciliation. |
| Typed lifecycle receipts | Library runtime + store/provider | The runtime emits closed proposal, authority, execution, and verification receipt families. Stores preserve append-only receipt history during active retention. Receipt truth still depends on the host participants and external observations that produced each event. |
| Scoped evidence reads | Host application, invoked by the library runtime | Reads are tenant-scoped and call the host's evidence-read authorization. Unknown and unauthorized references are intentionally not distinguished. The host remains responsible for authenticated tenant and consumer identity. |
| Protected erasure workflow | Host application + store/provider | Erasure requires a separately configured retention store, host authorization, idempotent payload/key destruction, and a content-free tombstone. Database backups, exports, telemetry, and external systems have independent retention policies. |
| Framework approval is not authority | Library Pydantic AI integration | Deferred metadata, message history, `ToolApproved`, and argument overrides are treated as untrusted continuation material. Execution still requires authority evidence recorded in the action store and all runtime checks. |

The library cannot upgrade a weak host implementation into a strong guarantee.
Custom stores, commitment providers, protection codecs, action ports, and
verifiers should be treated as security-relevant code and exercised with the
conformance helpers plus domain-specific adversarial tests.

## Enforcement and proof index

This index points from each claim to the code that enforces it and the closest
executable proof. A test establishes behavior for its fixture and version; it
does not certify an adopter's implementation.

| Rule | Enforced in | Executable proof or refusal path |
| --- | --- | --- |
| Boundary models reject coercion and extra fields | `models.ActionModel`; every public Pydantic boundary derives from it | `tests/unit/test_models.py`; package-wide strict-model tests |
| Canonical payloads are deterministic and reject floats | `canonical.canonicalize_v1` | `tests/unit/test_canonical.py` |
| Private snapshots are committed and protected separately from previews | `runtime.ActionRuntime.prepare`, `_load_private`; `canonical.commitment_payload_v1` | runtime tamper, missing-protection and leakage cases in `tests/unit/test_runtime.py` |
| Authority is bound, time-bounded and insufficient by itself | `authority.validate_authority_evidence`; `runtime.ActionRuntime.record_authority`, `execute` | binding, future/expired evidence and authority-expiry tests in `tests/unit/test_runtime.py` |
| Live permission is checked again before durable admission | `runtime.ActionRuntime.execute` calls `can_execute` before its final admission time | revocation and slow-authorization tests in `tests/unit/test_runtime.py` |
| `admitted_at` is re-stamped after live authorization | `runtime.ActionRuntime.execute` assigns a fresh clock value immediately before authority revalidation and `admit_execution` | controllable-clock expiry tests in `tests/unit/test_runtime.py` |
| The execution lease guards the durable write as well as dispatch | `runtime.ActionRuntime.execute`; official store `admit_execution` implementations; Stripe `PostgresStripeLedger.reserve` uses database `clock_timestamp()` | runtime lease-expiry tests; Stripe exercise `expired_admission` |
| Material state drift refuses the approved snapshot | host `StateResolverPort.resolve`, coordinated by `runtime.ActionRuntime.execute`; executor enforces `execution_precondition` | runtime drift tests; Stripe policy/snapshot drift qualification tests |
| Stripe policy changes invalidate approved work | `integrations.stripe.actions._RefundWorkflow.resolve`, `subscriptions._SubscriptionWorkflow.resolve`, and `credit_notes._CreditNoteWorkflow.resolve` compare `policy_fingerprint` | `tests/integration/stripe/test_*actions.py` and qualification fixtures |
| Lifecycle transitions and effect admission are atomic in official stores | `stores.memory`, `stores.postgres`, `stores.mysql`, `stores.sqlite` implementations of `compare_and_set` and `admit_execution` | store conformance; Stripe exercises `same_effect_race`, `conflicting_resource_race`, `normal_writer_exclusion` |
| Transport acceptance is not completion | `runtime.ActionRuntime.execute` maps accepted submission to verification-pending; only `_apply_verification` can reach verified completion | refund timeout and delayed-visibility examples; qualification recovery cases |
| Known no-effect results are returned; uncertain persistence is raised | Stripe repository protocols in `integrations.stripe.ports`; `PostgresStripeLedger` returns typed conflict/closed statuses and raises `StripePostgresHostError` for uncertain writes | Stripe exercises `immutable_intent`, `outcome_idempotence`, `lost_reservation_acknowledgement`; `tests/unit/test_stripe_postgres.py` |
| Error-message text never determines known versus uncertain outcome | Stripe group actions branch on typed gateway/repository statuses; PostgreSQL adapter uses distinct typed returns and uncertainty exceptions | `tests/unit/test_stripe_postgres.py`; adapter conformance tests |
| Ambiguous submissions are not blindly resent | `runtime.ActionRuntime._settle_execution`, `_settle_verification`; action definition resend policy | refund lost-acknowledgement tests and `examples/refund/test_example.py` |
| Verification attempts are leased and bounded | `runtime.ActionRuntime.reconcile`; store verification-claim transitions | runtime competing-reconcile and exhaustion tests |
| Recovery advice is read-only and suppresses unsafe execute steps | `recovery.recovery_condition`, `recovery.recovery_steps`; `runtime.ActionRuntime.read_recovery` | `tests/integration/stripe/test_recovery_view.py` including changed-owner uncertainty |
| Receipts are typed lifecycle records | `receipts.py`; `runtime.ActionRuntime` receipt builders; store append transitions | runtime receipt tests and cross-adapter store suites |
| Evidence reads and erasure are host-authorized | `runtime.ActionRuntime.read`, `export_evidence`, `erase`; host authorization/retention ports | cross-tenant, denied and erased cases in `tests/unit/test_evidence.py` and runtime tests |
| Evidence export is an unsigned internal-consistency projection | `evidence.ActionEvidenceBundle`, `evidence.validate_evidence_bundle` | digest, forgery, duplicate, dangling-link and erased-source cases in `tests/unit/test_evidence.py` |
| Framework approval does not become authority | `integrations.pydantic_ai.ActionCapability`; core runtime authority checks remain on every continuation | Pydantic AI deferred-tool and argument-tampering tests |

Stripe repository refusal codes are judged by the library-orchestrated
`integrations.stripe.conformance.assert_stripe_host_exercise`. Its twelve
scenario identifiers are stable lookup keys for the rule above; the deprecated
driver-attestation helper is not proof that a repository was exercised.

## Not enforced by the runtime

The runtime does not judge whether a model's explanation is truthful, detect
prompt injection, verify third-party provenance, decide which business fields
are material, or decide who should approve. A host may ask a model to extract
or propose values, but typed shape validation only establishes shape. The host
must resolve every actionable reference from authoritative state and implement
the authorization and drift rules used by its action.

## What a deployment owns

The deployment owns identity authentication and tenant mapping, current roles
and segregation policy, approval UI and channel authentication, commitment and
encryption keys, atomic business writers, external credentials, target-side
idempotency, reconciliation scheduling, operator routing, audit completeness,
telemetry redaction, retention and incident response. The conformance suites
exercise contracts at these seams; passing them does not operate those controls
for the adopter.

## Explicitly limited claims

### Exactly-once

The semantic-effect claim is local execution admission, not distributed
exactly-once delivery. A process can fail after the target accepts an effect and
before local settlement. The target must accept a stable idempotency identity,
and the verifier must query that identity. Where a target supplies neither,
the host must not claim exactly-once or automatically resend an ambiguous
effect.

### Receipts and auditability

Receipts are typed lifecycle evidence with correlation and participant labels.
They are not signed, hardware-attested, independently non-repudiable, or proof
that the labelled participant really acted. They are not a complete audit log:
events outside the runtime, failed event-sink delivery, privileged database
administration, target-side history, and application policy decisions may live
elsewhere. Erasure intentionally removes receipt content from the active
proposal record.

`EventSink` is a best-effort, at-most-once projection after action-store
persistence. Delivery errors are reduced to a safe warning and do not replace
the committed operation result. The runtime provides no event retry or durable
outbox; hosts that need durable delivery must project from persisted state.

An execution receipt identifies the configured governed executor, not the
authenticated caller that triggered an HTTP execution endpoint. The proposal
retains its requesting principal, but a host that permits interactive execution
must authorize the current trigger separately and record that identity in its
own audit plane when both actors matter.

### Canary and leakage scanning

The conformance scanner recursively checks the objects supplied to it for
caller-seeded exact literals and forbidden key fragments. It supports common
Python containers, Pydantic models, dataclasses, exceptions, strings, and byte
strings, and reports only labels and structural paths.

It is a regression test, not data-loss prevention. It does not automatically
inspect process memory, databases, logs, traces, network traffic, third-party
sinks, transformed or encoded values, high-entropy secrets, or values that the
test author failed to seed and include. Passing it does not prove that a system
contains no credentials or personal data.

### PII safety

`SafeReference` means syntactically constrained, not anonymous or safe to
publish. The library cannot recognize every personal identifier or business
secret. Hosts must use opaque references, minimize previews and results, keep
raw bank and payment coordinates inside protected snapshots or target systems,
configure telemetry redaction, and operate retention across backups and
downstream systems. Data protection impact assessments and data-subject request
handling remain application responsibilities.

### Standards and compliance

`internal/v0`, the canonical profile, the receipt vocabulary, and any
application-defined cross-system envelope are implementation details. They are
not an IETF, FIDO, Visa, Mastercard, AP2, or other public action/receipt
standard. The package provides no PSD2/PSD3, SCA, PCI DSS, SOC 2, EU AI Act, or
other legal or regulatory certification. It can preserve evidence useful to a
control program, but applicability, control design, legal interpretation, and
audit completeness remain with the deploying organization.

## Deployment assumptions

A production evaluation must, at minimum, provide:

- authenticated tenant, requester, confirmer, and evidence-consumer identities;
- host policy for preparation, decision, execution, read, and retention;
- canonical business-state resolution and an atomic mutation precondition;
- opaque semantic-effect identities and target-side idempotency where available;
- an authoritative query path and a durable reconciliation scheduler;
- a real protection codec and keyed commitment provider with managed keys;
- least-privilege runtime, migration, and retention database roles; and
- application-wide logging, privacy, backup, incident-response, and recovery
  controls.

Without those components, the package remains a lifecycle coordinator rather
than a safe financial-action system.

The least-privilege database-role requirement applies to production-oriented
adapters. SQLite cannot separate runtime and retention through database roles;
its official support is limited to local development, evaluation, tests, and
bounded single-writer deployments.
