# Threat model

Status: implemented experimental core with PostgreSQL, MySQL, Pydantic AI, and
conformance coverage. This is a security boundary description, not a claim of
production readiness, compliance, or complete threat mitigation.

## Implementation status

- The core runtime implements preparation, bound authority, live
  reauthorization, state re-resolution, atomic effect admission, governed
  execution, authoritative verification, scoped reads, and privileged erasure.
- The in-memory store supplies deterministic process-local guarded state. The
  optional PostgreSQL adapter supplies durable tenant-scoped compare-and-set,
  effect claims, verification leases, append-only evidence during active
  retention, migrations, and distinct runtime/retention privilege surfaces.
- The optional MySQL 8 adapter supplies InnoDB-backed tenant-scoped
  compare-and-set, digest-bound effect claims, lifecycle triggers, immutable
  migrations, and separate security-definer runtime/retention procedure lanes.
- The Pydantic AI Capability exposes command-only tool schemas and treats
  framework approval, message history, continuation metadata, and argument
  overrides as untrusted. It cannot create financial authority.
- The conformance module exercises store invariants, provider round trips,
  authority refusal, live reauthorization, drift, replay, authoritative
  verification, and seeded leakage detection. Passing generic conformance is a
  baseline; every action still needs domain and connector failure tests.
- Internal receipt golden vectors and local reference applications are
  interoperability and threat-test fixtures. They are not a public protocol,
  an independent audit, or external integration-time evidence.

## Assets and actors

Protected assets are canonical action state, authority evidence, semantic
effect identity, private snapshots, credentials, personal data, lifecycle
evidence, and authoritative completion status.

- A1 application developer registers a governed host operation and its ports.
- A2 requesting principal originates the authenticated goal.
- A3 proposing agent supplies typed intent, never business truth or authority.
- A4 confirming authority supplies a decision or verified mandate bound to an
  exact proposal.
- A5 governed executor applies the host-owned business mutation.
- A6 authoritative target system establishes the effect's actual state.
- A7 evidence consumer reads a scoped projection without execution authority.

Each actor has a distinct typed identity. A tool, detector, client-supplied
label, model message, framework approval object, or continuation token is not
promoted to one of these identities.

## Trust boundaries

1. Untrusted intent crosses from an agent or client into trusted host
   preparation. The host resolves canonical state and minimizes the preview.
2. A displayed proposal crosses into an authenticated authority channel. The
   host, not framework metadata or message history, authenticates the confirmer
   and records bound authority evidence.
3. An authorized proposal crosses into governed execution. The runtime invokes
   host ports to recheck authority, live permission, and material state before
   store-level effect admission; the executor must enforce the returned atomic
   host precondition at the mutation boundary.
4. Transport acceptance crosses into authoritative verification. Acceptance,
   timeout, provisional absence, or model prose is not business completion.
5. Private state crosses into persistence only through host-supplied protection
   and keyed commitment boundaries. Keys remain outside lifecycle storage.
6. Evidence crosses into readers, telemetry, exceptions, exported receipts,
   and conformance corpora only through scoped, minimized projections and host
   retention policy.
7. Optional PostgreSQL, MySQL, and Pydantic AI adapters cross dependency boundaries;
   the core neither imports nor silently activates them.
8. An application-defined cross-system transport crosses a second service's
   authentication, tenant, audience, replay, and canonical-state boundaries.
   The receiving service must establish those facts independently.

## Threats and implemented controls

| Threat | Implemented control | Residual owner or condition |
| --- | --- | --- |
| Authorization loss after preparation | The runtime calls the host's execution authorization immediately before admission and refuses the executor on denial. | Host application must implement correct policy and trusted identities. |
| Same-tenant unauthorized evidence read | Reads are tenant-scoped, call host read authorization, and do not distinguish unauthorized from unknown. | Host must prevent reference enumeration through other surfaces and supply the authenticated evidence consumer. |
| Material drift, including a mutation-boundary race | The runtime re-resolves state, terminalizes stale proposals, and passes a host precondition to effect admission/execution. | State resolver must identify every material field; executor/target must enforce the precondition atomically. |
| Authority replay against a fresh or different proposal | Evidence is bound to tenant, action/version, proposal, semantic effect, keyed commitment, audience, assurance, issue time, and expiry. | Authority service and evaluator must authenticate the principal and apply separation-of-duty policy. |
| Concurrent decisions or execution claims | Tenant-scoped revision transitions and atomic semantic-effect admission are implemented by conforming stores; PostgreSQL and MySQL use transactions and lock-backed constraints. | Custom stores must pass conformance; target-side duplicate delivery remains separate. |
| Partial external or itemized failure | Partial success is accepted only for an action declared itemized and preserves per-item outcomes. | Host must choose the correct effect shape and verifier must report every item accurately. |
| Provisional absence mistaken for final absence | Provisional absence stays verification-pending. Resend eligibility requires declared final absence, settling-boundary passage, target idempotency, and explicit action policy. | Connector owns the truth and timing of final absence; host owns resend policy. |
| Ambiguous external outcome or timeout | Accepted and failed-unknown effects enter bounded, lease-admitted verification rather than blind resend. | Host needs a durable scheduler and the target must support query by stable effect identity. |
| Tenant crossover in lifecycle persistence | All store operations include tenant reference; PostgreSQL and MySQL primary/foreign keys and routines preserve it; cross-tenant lookups return no record. | Database administrators and incorrectly shared application credentials remain privileged threats. |
| Forged client or framework approval | Pydantic AI continuation material is routing-only; runtime execution still requires server-recorded authority and trusted dependency context. | Host must authenticate dependencies and keep authority issuance outside model/client control. |
| Expired proposal or evidence | Runtime checks proposal and evidence time bounds again before admission and terminalizes or blocks invalid state. | Correct timezone-aware clocks and bounded skew remain operational dependencies. |
| Protected payload leakage from lifecycle storage | Runtime calls a protection codec before store creation, verifies the separate keyed commitment on load, and hides erasure-pending content. | Host owns encryption, key management, rotation, access controls, backups, and side-channel prevention. |
| Receipt/evidence mutation | Store validation plus PostgreSQL/MySQL privilege lanes preserve active evidence; MySQL routines also require strict append-only arrays and proposal-bound evidence/receipt fields. Corrections are new linked receipts. | Receipts are unsigned host assertions. A compromised runtime credential can append structurally bound forged material because the database cannot authenticate or cryptographically verify the named issuer. |
| Credentials or unnecessary PII in generic surfaces | Closed minimized schemas plus recursive seeded-canary tests catch known regressions in supplied corpora without echoing values. | Host must identify all sensitive data, include every real surface in tests, redact telemetry, and enforce downstream retention. |

## Evidence integrity and retention

Proposal, authority, execution, and verification are separate internal receipt
families with correlation, causation, observation time, participant labels, and
explicit status. During active retention, corrections and supersession are new
linked records rather than receipt mutation. A separate host-authorized
retention boundary can destroy protected content and replace the active record
with a content-free lifecycle tombstone.

Receipts are neither signed nor independently non-repudiable. They do not prove
that the recorded participant identity was authenticated correctly, that
application policy was correct, that every relevant event was captured, or
that an external effect completed without an authoritative verifier. Backup,
warehouse, telemetry, and target-system retention remain outside runtime
erasure. See [guarantees and limitations](guarantees-and-limitations.md).

## Residual system risks

- **Incorrect or malicious host ports.** The library deliberately trusts the
  host's canonical resolver, authorization, authority evaluator, executor,
  verifier, retention policy, and identities. Conformance catches selected
  contract violations, not arbitrary malicious behavior.
- **External atomicity gap.** A target effect and the local lifecycle are not
  one transaction. Stable target idempotency plus authoritative query manages
  ambiguity; the library does not provide distributed exactly-once execution.
- **Weak semantic identities.** Colliding identities can suppress legitimate
  actions; unstable identities can admit duplicates; personal identifiers can
  leak through evidence. The host must define opaque durable identities.
- **Target or verifier falsehood.** `verified` reflects the configured
  verifier's observation. A non-authoritative API, stale replica, compromised
  target, or incorrect final-absence declaration invalidates the claim.
- **Cross-system transport.** The library does not define mutual
  authentication, encryption, key distribution, replay protection, audience
  semantics, dispute handling, or protocol negotiation for service-to-service
  envelopes. Each receiver must reauthenticate, reauthorize, bind its own
  audience, and re-resolve state.
- **Expected-effect binding durability.** The two-service example keeps its
  opaque request bindings in process because every component is intentionally
  in memory. A production initiator must persist the approved request binding
  across restarts and compare the receiver's authoritative query before
  declaring completion.
- **Triggering actor attribution.** Execution receipts name the governed
  executor. An HTTP or job trigger is a separate host actor: the host must
  authorize it and record it independently when accountability requires both
  the trigger and the service mutation identity.
- **Database and backup privilege.** A table owner, migrator, superuser, backup
  operator, or compromised database can read or mutate stored material outside
  the application roles. Deployment controls and monitoring remain required.
- **Runtime database credential.** MySQL routines prevent direct callers from
  changing approval truth, erasure markers, existing evidence, or effect-claim
  identity. They validate bindings on newly appended evidence and receipts,
  but do not verify issuer signatures. The host must authenticate or
  cryptographically verify issuers before persistence and protect the runtime
  credential as a trusted application boundary.
- **Key and recovery failure.** Lost protection or commitment keys can make
  active proposals unusable; compromised keys can expose snapshots or permit
  forged commitments. Key lifecycle and disaster recovery are host concerns.
- **Availability and denial of service.** Attackers can create proposals,
  consume verification attempts, exhaust provider quotas, or block authority
  and target systems. Rate limits, quotas, queues, and operational recovery are
  not supplied by the core.
- **Model and approval-interface manipulation.** A model can misdescribe an
  action and a UI can render a preview deceptively. Hosts must render the
  minimized proposal from trusted server data and design an authenticated,
  accessible confirmation experience.
- **Privacy beyond the active record.** The library cannot discover all PII or
  erase logs, traces, exports, caches, backups, model-provider data, or external
  targets. Organization-wide privacy controls remain necessary.
- **Audit and compliance interpretation.** Internal receipts and controls can
  support an audit program but are not a public receipt standard, complete
  audit system, regulatory mapping, or certification.

Until the host-specific controls above are implemented and the full action,
store, connector, framework, privacy, and failure-path tests pass, the package
must not be treated as sufficient protection for production financial effects.
