# Agentic-commerce protocol watch

Last reviewed: **13 September 2026**. This is a compatibility watch, not a
support claim. Re-check primary sources before using any version below.

| Surface | Observed baseline | Relevant boundary | Implementation trigger |
| --- | --- | --- | --- |
| AP2 | v0.2; purchase and delegation mandate model | A merchant host could verify a mandate before creating local authority. The runtime does not parse or verify AP2. | A named merchant adopter supplies its exact AP2 profile, trust anchors, replay owner and one governed purchase operation. |
| ACP | Stable specification dated 17 April 2026; repository labels the project beta | Delegated-payment settlement, refunds, chargebacks and compliance remain merchant/PSP responsibilities. | A named adopter needs a particular ACP artifact accepted at a documented host boundary. |
| UCP | Current announcement/version dated 25 August 2026 | UCP capabilities and transports may reach a merchant endpoint; local state, permission, execution and recovery remain host concerns. | A named UCP merchant flow demonstrates a reusable boundary not expressible through existing ports. |
| Visa | Trusted Agent Protocol and a card specification/SDK for Machine Payments Protocol are public | Verified agent/network facts remain host-owned inputs; they are not local business authority or completion proof. | An adopter provides a verified artifact, issuer/audience rules and retention requirements. |
| Mastercard | Public participation across AP2, UCP and ACP is documented | Network participation does not establish an AP2-native SDK or a universal merchant adapter. | A shipped interface and adopter-owned flow define an exact integration contract. |
| Anthropic commerce-agents | Repository observed at commit `fd4d59224ab96b43c6dc6888207c67b3bd5a24cf` (31 August 2026) | Its merchant apply boundary is a useful comparison and example target. Its age does not yet establish maintenance history. | Re-pin before rerunning the optional upstream compatibility exercise. |

## Quarterly check

1. Record exact spec/repository versions and dates from primary sources.
2. Note schema, trust, replay, lifecycle or completion changes relevant to the
   host/runtime boundary.
3. Check whether Visa or Mastercard has shipped a concrete interoperable SDK;
   do not infer one from partnership announcements.
4. Record adopter demand and the exact artifact or operation requested.
5. Leave the package dependency graph unchanged unless the implementation
   trigger in the table is satisfied and separately reviewed.

## Sources

- [AP2 specification](https://github.com/google-agentic-commerce/AP2/blob/main/docs/ap2/specification.md)
- [AP2 releases](https://github.com/google-agentic-commerce/AP2/releases)
- [ACP delegated payment](https://agentic-commerce-protocol.com/docs/commerce/specs/payment)
- [ACP repository](https://github.com/agentic-commerce-protocol/agentic-commerce-protocol)
- [UCP announcements](https://ucp.dev/documentation/announcements/)
- [Mastercard agentic-commerce overview](https://www.mastercard.com/us/en/news-and-trends/stories/2026/agentic-commerce-rules-of-the-road.html)
- [Visa Trusted Agent Protocol](https://corporate.visa.com/en/sites/visa-perspectives/newsroom/visa-unveils-trusted-agent-protocol-for-ai-commerce.html)
- [Visa card specification SDK for MPP](https://corporate.visa.com/en/sites/visa-perspectives/innovation/visa-card-specification-sdk-for-machine-payments-protocol.html)
- [Anthropic commerce-agents](https://github.com/anthropics/commerce-agents)

The absence of refund, cancellation, retry or completion semantics from a
purchase specification is an observation about its current text, not a claim
that the specification forbids future support.
