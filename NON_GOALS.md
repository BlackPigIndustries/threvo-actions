# Non-goals

`threvo-actions` is a control lifecycle for consequential application actions.
These boundaries keep it useful without turning it into a payment network,
agent framework, policy engine, or compliance product.

| Non-goal | Why it stays outside | Nearest supported boundary |
| --- | --- | --- |
| Buyer-side ACP, AP2 or UCP client | Buyer discovery, checkout negotiation and mandate exchange are separate protocol roles. | A merchant host can map already verified protocol facts into its own authorization port. |
| x402, MPP or payment transport | The runtime does not exchange credentials, select rails, price requests or settle funds. | A governed executor may call a host-owned payment client after every runtime gate passes. |
| Stripe, Visa or Mastercard abstraction | Provider and network semantics differ and remain authoritative at their own boundaries. | Typed connectors translate specific supported operations without hiding their completion rules. |
| Card-network agent identity | The host owns protocol verification, trust anchors, rotation and tenant/principal mapping. | `ProposingAgent.reference` is an opaque host-authenticated reference; a future richer evidence model is trigger-bound. |
| Prompt-injection detection or third-party-text fencing | Those controls depend on the surrounding agent, retrieval and UI threat model. Structural validation alone does not solve prompt injection. | Strict command models reject malformed identifiers, and prepare/resolve reload authoritative host state instead of trusting model prose. |
| Signature or mandate verification | Supporting an external format safely requires exact versions, issuers, audiences, replay state and custody policy. | Host authorization ports can consume verified facts. The [artifact design](https://blackpigindustries.github.io/threvo-actions/design/external-authority-artifacts/) is held until an adopter supplies a real format. |
| Authorization policy engine | The library cannot know organization roles, segregation rules, budgets or revocation state. | It invokes host authorization at prepare, decision and execution and binds evidence to one proposal. |
| Distributed exactly-once delivery | A remote target and local database cannot generally commit atomically. | Local semantic-effect admission, target idempotency and authoritative reconciliation prevent blind retries and expose uncertainty. |
| Audit completeness or compliance certification | Runtime records cannot attest to events, identities or copies outside their custody. | Typed receipts and an explicitly unsigned evidence projection support a host-owned control and audit program. |

Adding a new integration requires a named operation, an authoritative query,
known failure semantics, a durable host owner and evidence from an adopter. A
protocol announcement alone is not an implementation trigger.
