# AP2 compatibility decision

Status: **defer an AP2 adapter until a named host needs an AP2 verifier for a
specific purchase flow**.

This decision was made against Agent Payments Protocol v0.2 and the official
AP2 repository at commit
[`e1ea56db72a6385bce3e5c1112b3a56ce60acb43`](https://github.com/google-agentic-commerce/AP2/commit/e1ea56db72a6385bce3e5c1112b3a56ce60acb43),
observed on 12 September 2026. The protocol is moving independently of this
library, so any implementation must pin and re-review the exact AP2 version it
accepts.

## Decision boundary

AP2 secures an agent-performed purchase. It defines Shopping Agent, Credential
Provider, Merchant, Merchant Payment Processor and Trusted Surface roles,
along with linked Checkout and Payment mandates. A mandate verifier evaluates
cryptographic delegation and returns a protocol receipt. AP2 leaves catalog,
checkout transport, retention and dispute retrieval outside its current
scope.

`threvo-actions` governs an application mutation after the host has resolved
its business state. Its `AuthorityEvidence` is an internal, unsigned
`internal/v0` record that must match tenant, proposal, semantic effect,
commitment, audience and channel assurance. The record is deliberately
insufficient by itself: the host still performs live authorization before
execution and verifies the result against an authoritative target.

These systems can compose, but they are not equivalent. AP2 may establish that
a user delegated a particular purchase to an agent. It does not establish an
employee's current right to refund that purchase, cancel its subscription or
issue a credit note.

## Supported receiving role

The only candidate integration identified by this study is a host acting as
an AP2 **Merchant** or a delegate of that Merchant. Before preparing a governed
purchase action, an optional adapter could verify a closed Checkout Mandate
and its delegation chain. It could then emit a minimized, locally bound
authority input for a host policy evaluator.

The adapter must not present `threvo-actions` as a Shopping Agent, Credential
Provider, payment network or Merchant Payment Processor. It must not provision
credentials, execute a payment, assert settlement, or manufacture a Trusted
Surface.

## Responsibility map

| Concern | Optional AP2 verifier | Host application | Action runtime |
| --- | --- | --- | --- |
| Exact AP2 schema/version | Reject unknown `vct` values and algorithms | Pin the accepted profile | No AP2 knowledge |
| Issuer/provider trust | Validate the configured credential issuer or trusted agent provider chain | Own trust anchors and rotation policy | No trust-anchor store |
| Holder/delegation proof | Verify OpenID4VP/SD-JWT presentation, key binding, disclosures and mandate chain | Authenticate the local request channel | Store only minimized bound evidence if configured |
| Audience and time | Validate verifier audience, `iat`, `nbf` and `exp` at an injected time | Define accepted audiences and clock policy | Re-check local authority expiry at execution |
| Checkout binding | Verify merchant-signed checkout JWT and checkout hash | Resolve the checkout to the correct tenant and canonical business object | Bind the resulting proposal commitment and semantic effect |
| Payee, amount and currency | Validate the Payment Mandate against the closed checkout and local merchant identity | Apply company limits, budget and segregation rules | Preserve the approved proposal binding |
| Replay | Enforce AP2 nonce/presentation and open-mandate consumption rules for the selected profile | Persist protocol replay state atomically | Prevent concurrent execution of the same semantic effect |
| Principal mapping | Return a verified external subject and trust-chain facts | Map that subject to a tenant-scoped requesting principal and confirming authority | Accept only host-authenticated participants |
| Live permission | Expose verified claims, never an unconditional allow decision | Re-evaluate current business authorization immediately before execution | Call the host authorization port after admission waits |
| Completion | Return AP2 acceptance/rejection receipts where the role requires them | Execute through the payment processor and retain provider references | Verify the governed effect against the authoritative target |
| Evidence retention | Return minimized verification facts plus source references | Set retention, privacy and dispute-retrieval policy | Retain its own `internal/v0` lifecycle receipts |

## Proposed boundary model

A future verifier should expose strict, frozen Pydantic models and a Protocol,
not raw JWT dictionaries. Its successful result needs, at minimum:

- the exact AP2 profile and `vct` values accepted;
- the trusted issuer or agent-provider reference and verified subject;
- verifier audience, issuance and expiry times;
- checkout hash, merchant/payee reference, amount and currency;
- mandate and presentation identifiers needed for replay control;
- a digest or protected reference for retained source evidence; and
- the verification time and verifier revision.

The host then maps those facts into its tenant, principals and local action.
Only after that mapping may it create `AuthorityEvidence` bound to a concrete
`proposal_instance_reference`, `semantic_effect_reference` and
`proposal_commitment`. The AP2 token itself must never be copied into runtime
receipts or logs.

## Valid journey

1. A Shopping Agent receives a merchant-signed checkout and obtains the
   required Checkout and Payment mandates through a Trusted Surface.
2. The host's AP2 verifier validates the exact version, signatures,
   delegation, holder binding, disclosures, audience, time window, checkout
   hash, payee, amount, currency and replay state.
3. The host maps the verified subject and merchant to one tenant and applies
   its live purchase policy.
4. The host prepares a governed purchase proposal and records local authority
   evidence bound to that proposal. The external mandate is supporting input,
   not the runtime's authorization decision.
5. Immediately before execution, the host rechecks current permission. The
   runtime executes once and verifies completion against the authoritative
   payment target.
6. The host retains AP2 evidence and receipts under its own dispute policy;
   the runtime retains its separate lifecycle evidence.

No current Stripe refund, subscription-cancellation or credit-note action is
the purchase action in this journey. Supporting it would require a new,
explicitly designed purchase action and a host already participating in AP2.

## Mandatory rejection journeys

A future adapter cannot qualify without deterministic tests for these cases:

| Input | Required result |
| --- | --- |
| Forged mandate, checkout or delegation signature | Reject before proposal preparation; do not convert it to local authority evidence |
| Wrong verifier audience | Reject even when every other claim is valid |
| Wrong merchant/payee or mismatched checkout hash | Reject; do not let local tenant mapping repair the mismatch |
| Amount or currency outside the closed checkout or delegated constraints | Reject |
| Expired or not-yet-valid evidence | Reject using an injected clock |
| Replayed presentation or autonomous mandate use | Reject through the host's durable replay store |
| Valid external subject that cannot map to exactly one local tenant/principal | Reject |
| Cryptographically valid purchase mandate presented as refund, cancellation or credit-note authority | Reject as the wrong business authority |
| AP2 verification succeeds but current local policy denies the action | Return the host denial; do not treat signature validity as permission |
| Payment processor accepts but settlement cannot be authoritatively verified | Preserve an uncertain/pending runtime outcome; do not infer completion from AP2 acceptance |

## Implementation gate

Build the optional verifier only when all of the following are named and
available:

1. an adopter operating as Merchant or its explicit verification delegate;
2. one purchase action with defined local policy and authoritative target;
3. the exact AP2 profile, trust anchors and credential/delegation model;
4. a durable replay-state owner and evidence-retention owner; and
5. sandbox fixtures covering the valid and rejection journeys above.

Until then, keep AP2 outside the package dependency graph. Continue to accept
verified external authorization through host-owned policy ports. This keeps
the runtime compatible with AP2 adoption without claiming protocol
conformance or weakening the distinction between cryptographic validity,
business permission and completed effect.

## Sources

- [AP2 v0.2 specification](https://ap2-protocol.org/ap2/specification/)
- [AP2 Agent Authorization Framework](https://ap2-protocol.org/ap2/agent_authorization/)
- [AP2 Payment Mandate](https://ap2-protocol.org/ap2/payment_mandate/)
- [Official AP2 repository](https://github.com/google-agentic-commerce/AP2)
