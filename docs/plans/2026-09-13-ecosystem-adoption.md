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
| 5 | T9 adoption host | The repository owner selected the maintainer-owned Threvo application as the first test bed. The brief and protocol preserve the distinction between realistic integration evidence and independent adoption. No external outreach is planned. |
| 6 | T3 external authority artifact | Design complete; code held. Exit requires one named adopter's exact artifact, verifier, replay, retention and erasure contract. |
| 7 | T5 framework access | Split design complete. Sequence is Agent SDK recipe, authenticated reference MCP host, then a generic packaged MCP surface only after two hosts prove the common contract. |
| 8 | T8 proposing-agent attestation | Folded into the external-artifact design and held. A single optional string is rejected as premature because real formats may require multiple artifacts, rotation and verifier revision. |

## Immediate Threvo test-bed pipeline

1. Complete the start conditions in the
   [Threvo test-bed brief](../testing/threvo-stripe-test-bed.md).
2. Select one existing, semantically matching Threvo operation and map its
   repository, normal writer, authorization and recovery boundaries.
3. Run the refund repository exercise and record every failed, assisted and
   unexercised scenario.
4. Run the sandbox lifecycle, lost-acknowledgement recovery and evidence export.
5. Repeat with subscription cancellation or a supported credit-note disposition
   only when the application already has the matching business operation.
6. Use the observed artifact and framework requirements to decide whether T3,
   the Agent SDK recipe, or the MCP reference host has a real implementation
   trigger.
7. Reassess the stabilization hold. Do not publish held surfaces merely because
   their design documents are complete.

The Threvo exercise is the next application-integration task and is outside this
library release. It can expose API and operational friction, but it does not end
the independent-adoption gate or authorize the held T3, T5 and T8 surfaces
without their documented concrete requirements.
