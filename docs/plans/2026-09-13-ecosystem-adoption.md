# Ecosystem and adoption execution record

Date: 13 September 2026. Branch: `develop`. Publication remains subject to the
[0.4 stabilization policy](../stabilization.md).

This plan adapts the reviewer proposals to the evidence available in the code
and primary protocol sources. “Implemented” below means the repository artifact
or executable example exists. It does not mean an outside adopter, protocol
conformance, or production qualification has been established.

| Order | Work | Disposition and exit |
| --- | --- | --- |
| 1 | T1 positioning + T6 non-goals | Implemented in the root README, `NON_GOALS.md`, the Stripe coding-agent reference and the protocol watch. Claims distinguish protocol facts from inference and claim no protocol support. |
| 2 | T2 guarantees index | Implemented by extending the existing guarantees page with code locations, executable proofs, runtime exclusions and deployment ownership. The Stripe conformance module links back to it. |
| 3 | T4 merchant `apply_change` | Implemented as a strict-Pydantic example with drift, provisional-absence recovery and competing-proposal tests. A pinned probe loads the official `commerce-agents` retail fixture, preserves `check_apply_change`, and passes its staged change through this runtime. |
| 4 | T7 protocol watch | Implemented as a dated primary-source baseline. Review quarterly; an announcement alone never opens implementation. |
| 5 | T9 outside adopter | Qualification brief, intake, privacy boundary and outreach draft implemented. Exit remains external: a maintainer must identify and contact a consenting host owner, then run the existing protocol. No participant is named today. |
| 6 | T3 external authority artifact | Design complete; code held. Exit requires one named adopter's exact artifact, verifier, replay, retention and erasure contract. |
| 7 | T5 framework access | Split design complete. Sequence is Agent SDK recipe, authenticated reference MCP host, then a generic packaged MCP surface only after two hosts prove the common contract. |
| 8 | T8 proposing-agent attestation | Folded into the external-artifact design and held. A single optional string is rejected as premature because real formats may require multiple artifacts, rotation and verifier revision. |

## Immediate outside-adoption pipeline

1. Select prospects using the
   [outside-adopter brief](../testing/stripe-adopter-recruitment.md).
2. Obtain the host owner's consent and complete the intake record without
   credentials or customer data.
3. Run the refund repository exercise and record every failed, assisted and
   unexercised scenario.
4. Run the sandbox lifecycle, lost-acknowledgement recovery and evidence export.
5. Repeat with subscription cancellation or a supported credit-note disposition.
6. Use the observed artifact and framework requirements to decide whether T3,
   the Agent SDK recipe, or the MCP reference host has a real implementation
   trigger.
7. Reassess the stabilization hold. Do not publish held surfaces merely because
   their design documents are complete.

No engineering task can manufacture the missing consenting adopter. The
repository now contains the explanation, proof paths, demonstration and intake
materials needed for that conversation; outreach and external execution are
the remaining product work.
