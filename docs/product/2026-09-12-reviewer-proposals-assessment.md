# Assessment of the 0.3.2 review and proposed next steps

Date: 2026-09-12. This assessment is historical rationale. Its accepted recommendations are now incorporated into the [final implementation plan](../plans/2026-09-12-0935-feat-stripe-adoption-recovery-plan.md), whose scope, unit definitions, and execution order supersede the recommendations below. No implementation or production use is authorized by this assessment itself.

## Recommendation

Keep the three priorities, but deliver them as a complete refund adoption path first. Move host conformance and recovery into that path before asking an outside application to rely on it. Add compatibility/positioning work immediately, explicit sibling-effect recovery information with U5, and a small evidence export after recovery. Investigate approval-channel interoperability and AP2 without making either a prerequisite for proving the existing product.

The review's strongest insight matches the intended product: encode expertise about consequential mutations into understandable contracts, defaults, refusals, and diagnostics. Its evidence supports the importance of those failure modes. It does not establish willingness to adopt, willingness to pay, universal prevention of duplicate effects, or readiness for unattended financial operations.

## Evidence and qualifications

I read the pasted 0.3.2 review and the full [linked assessment in Chrome](https://claude.ai/code/artifact/e21ad8e9-3aeb-4f9d-b542-b6667f1e146b?via=auto_preview). Code inspection used the same local baseline as the plan. The reviewer reports 66 tests and reproduced interleavings; those external tests were not supplied or rerun here. The observations below distinguish source inspection from the reviewer's reported execution evidence.

| Proposal or claim | Assessment | Consequence |
| --- | --- | --- |
| Distinct losing proposals report `replayed` while the winner is active | Confirmed in source: `runtime.py` reloads the losing proposal on admission conflict. The existing regression test covers concurrent advancement of the same proposal. `docs/releases/0.3.1.md` explicitly documents the distinction | Add deterministic distinct-proposal tests and authorized sibling recovery information; preserve the documented legacy result unless a deliberate compatibility decision changes it |
| Graduate `ActionApplication` immediately | Disagree with immediate graduation. Both the bundled skill and `docs/versioning.md` explicitly require exact pins and equivalence tests for experimental use. A 120-day evaluation/support window and separate evidence-based promotion gates already exist | Clarify the supported entry path, maintain compatibility, and execute the existing gates. Do not replace them with a 60-day calendar test |
| A fake repository can satisfy the Protocol without atomicity | Agree. Types cannot establish cross-connection transaction behavior or participation by ordinary writers | Prioritize U2/U3; deliberately defective repositories must fail the corresponding scenario |
| Ship a receipt-chain verifier | Agree with inspectable evidence, qualify the proposed guarantee. `receipts.py` stores typed correlation/causation links, not a signed hash chain. `docs/features/receipts.md` explicitly calls receipts unsigned host assertions | Start with a versioned evidence bundle and consistency validation. Cryptographic provenance is a separately designed future capability |
| Routing-layer interoperability | Agree with the architectural boundary. A channel callback is not sufficient authority | Build one authenticated host recipe when a partner needs it, with server-owned bindings and current authorization |
| AP2 maps almost one-to-one to our models | Only a conceptual analogy. Current official AP2 defines Checkout and Payment mandates, cryptographic bindings, roles, and trust models; these are not equivalent to our locally bound authority record | Commission a bounded compatibility study now; defer a shipping adapter until a concrete receiving role and use case exist |
| Refunds should lead adoption | Agree with sequencing, not removing supported groups | Keep subscription/credit-note regression coverage and correctness fixes; qualify their durable integration after the first refund path works |
| Eight releases means eight API changes; zero stars means zero users | These inferences are not supported by those counts | Measure actual compatibility changes and named adoption evidence. Absence of evidence in our adoption ledger remains a real gap |
| Choose one name | Agree | Use “Threvo Actions — governed actions for consequential application changes,” with Stripe as the first domain integration and agent support as an integration capability |

Two external corrections matter. HumanLayer's current [official site](https://www.humanlayer.dev/) and [repository](https://github.com/humanlayer/humanlayer) emphasize a coding-agent workspace. The general case for approval-channel interoperation remains, but the assessment's vendor positioning should not decide our first adapter. Its claim that competing tools lack comparable evidence also needs a feature-level comparison before publication.

The current [AP2 specification](https://ap2-protocol.org/ap2/specification/) names Checkout and Payment mandates, and the [authorization framework](https://ap2-protocol.org/ap2/agent_authorization/) defines trusted issuers/providers and credential/delegation verification. A shopping payment mandate does not inherently authorize a merchant employee to issue a refund. The adapter study must establish that business-authority relationship rather than rename fields.

## Recommended additional tasks

### A1. Publish a coherent compatibility and positioning contract

**Order:** Before U1; small documentation and compatibility-checking task, not a release freeze.

**Deliver:** One product description across `README.md`, `pyproject.toml`, docs, and bundled skill guidance. A supported-versus-experimental table covering Python names, operation meanings, stored data, and wire formats; exact-pin upgrade guidance; documented support ownership. Reconcile stale release labels against the actual released checkout before editing them. Keep the convenient supported `Action` and Stripe facade prominent; present `ActionApplication` with its existing conditions.

**Verification:** Existing constructor examples and serialized fixtures remain valid. Add a targeted check that documentation/skill examples import from the surface they claim to support. Review proposed public names in U4/U5 before publishing them. Bundle additive releases around usable milestones; continue necessary bug/security fixes. Do not promise a new support duration without an accountable owner.

**Suggested files:** `docs/versioning.md`, `docs/features/receipts.md`, `README.md`, `pyproject.toml`, `.agents/skills/threvo-actions/SKILL.md`, and existing API/golden tests. Repository description changes can follow the reviewed local wording through an authorized publication step.

### A2. Explain contention with another proposal for the same effect

**Order:** Characterization in U1; public information in U5 before U6/U8 depend on recovery advice.

**Deliver:** An additive relation in `ActionRecoveryView` distinguishing the requested proposal from the effect owner. Use existing `ActionStore.get_effect_claim_owner`; no new mandatory store method. Preserve the losing proposal's lifecycle and reference. Expose an owner's reference/status only after a separate applicable `can_read` check and exact tenant/action binding. For a hidden, erased, missing, or unresolvable owner, report that effect ownership prevents dispatch without revealing private sibling data or suggesting completion.

An authorized losing proposal with an already-owned effect must not receive the generic “request execution” recommendation. A visible owner may be executing, pending verification, unresolved, or settled. Only the appropriate owner operation is suggested, and all runtime checks still apply. A previously consumed effect is not permission to create another intent.

**Verification:** Barrier-controlled races with two distinct proposals; one provider mutation; winner active and winner settled; verification pending/unresolved; same-proposal replay; denied sibling read; erased sibling; owner changes between reads; no winner payload copied into the loser's result. Read the effect relation consistently enough to detect conflicting observations and return uncertainty instead of a fabricated snapshot. The result remains advisory, never an atomic authorization decision.

**Files:** `src/threvo_actions/runtime.py`, planned `recovery.py`, `tests/unit/test_runtime.py`, planned recovery tests, and recovery/versioning documentation. Retain existing `OperationOutcome.REPLAYED` semantics for compatibility; a later change needs explicit migration treatment.

### A3. Export an inspectable evidence bundle

**Order:** After U5 and the refund part of U7; include a minimal bundle in the first partner handoff. This must not delay basic host conformance.

**Deliver:** A strict versioned `ActionEvidenceBundle`, an authorized `export_evidence` runtime method following `read` conventions, and a pure `validate_evidence_bundle` function with a typed report. Keep these in a dedicated `evidence.py` module. Include source runtime/schema versions, observed proposal revision, ordered receipts, safe action/effect/proposal references, safe preview/result where authorized, export time, and explicit omissions/retention state. Any retained binding or authority summary needs a separate allowlist; never serialize `StoredProposal` wholesale or export protected snapshot ciphertext by default.

Validation covers declared schema, identifiers and internal references, duplicate receipts, supported lifecycle/receipt consistency, and malformed or incomplete fields. It does not reconstruct missing historical approval content, assume timestamps form a trusted total order, or treat a missing legacy field as evidence of fraud. Reports distinguish consistent, inconsistent, and insufficient evidence. Host case observations are separately attributed attachments, not newly invented runtime receipts. Add a simple human-readable rendering with the reference application; JSON remains the interchange artifact.

**Verification:** Cross-tenant/denied/erased exports, legacy attribution gaps, unknown schema rejection, dangling links, invalid duplicates, late observations, and private-data leakage. A checksum can detect changes relative to a trusted retained digest, but a self-contained file plus a recomputed checksum cannot prove authenticity or completeness. Document that a coherently rewritten unsigned bundle may pass consistency checks.

**Files:** New `src/threvo_actions/evidence.py`, runtime export method, `tests/unit/test_evidence.py`, integration export tests, `docs/reference/evidence.md`, and reference-host rendering. Review the new external schema explicitly because current `internal/v0` receipt JSON is experimental; include its exact version and reject unsupported embedded versions.

**Deferred:** Signed checkpoints, independently anchored receipt histories, key rotation/revocation, and historical non-repudiation. Those need a separate threat model and custody/retention design. Ask the partner's finance/audit stakeholder to assess the bundle's usefulness; do not label it “auditor acceptable” or compliant in advance.

### A4. Demonstrate one approval-channel integration

**Order:** After the refund path works; alongside U8 if a partner already needs it, otherwise after the first outside exercise.

**Deliver:** One host-owned callback recipe for the chosen supported channel. The application persists an immutable request-to-proposal binding before sending a request. The callback authenticates the transport and human identity, resolves tenant/proposal on the server, checks current approver eligibility and required channel assurance, then constructs `AuthorityEvidence` and calls the existing authority-recording method. It never copies authority, commitment, or tenant from untrusted callback fields. Sending requests, escalation, and notification policy remain host/routing-provider responsibilities.

**Verification:** Forged callback, wrong workspace/user, replayed callback, denial, expired request, changed proposal, revoked approver, and self-approval where host policy forbids it. A valid channel event with insufficient assurance must fail; the adapter cannot silently elevate assurance. Framework or channel approval alone never triggers direct SDK dispatch.

**Files:** A new `examples/approval_channel/` recipe, `docs/integrations/approval-channels.md`, and channel-specific integration tests. Select and verify the actual vendor API at implementation time. Do not add a universal routing abstraction or a HumanLayer dependency simply because the review names it.

### A5. Write a bounded AP2 compatibility decision

**Order:** Research document early; no dependency from U1–U10 on an AP2 implementation.

**Deliver:** `docs/design/ap2-compatibility.md` pinned to an exact specification/repository revision. Map the receiving role, trust anchors, credential and holder/delegation verification, audience and expiry, checkout/payee/amount binding, replay controls, local tenant/principal mapping, and retained evidence. Identify which checks belong in an optional verifier and which remain live host policy. Record unsupported/refund-authority cases explicitly and conclude implement/defer with a named use case.

**Exit:** A field mapping alone does not pass. The study must include at least one valid authorization journey and rejection journeys for forged signature, wrong audience/payee, expired evidence, replay, and a legitimate purchase mandate incorrectly presented as refund authority. A future adapter consumes verified external authorization into the current host policy boundary; it does not turn an AP2 signature into unconditional permission or a settlement guarantee.

### A6. Bring the outside-host exercise forward

**Order:** Prepare the partner brief now; perform the exercise as soon as the refund path passes database and sandbox checks, before waiting for every billing deliverable.

**Deliver:** Split U10 into early qualification materials and final documentation. Describe a concrete refund workflow, host owner, deployment/version, normal writer paths, custody, recovery ownership, and evidence collection. Use an actual outside repository and its normal database operations. Log assistance and failures. Public metrics, downloads, or internal reviewer tests cannot substitute for this evidence.

The first exercise should prove the adopter can integrate, refuse stale/unauthorized work, recover an accepted-but-unanswered submission, interpret pending/unresolved outcomes, and inspect evidence. An owner-approved bounded production observation can follow sandbox qualification. No user recruitment, contact, credentials, live operations, or production-readiness claim is implied by writing this task. A month of use is useful observation, not universal proof; preserve the existing formal adoption/support gates.

## Proposed execution order relative to the original three priorities

The original U1–U10 identifiers remain useful. Split their deliverables by action where noted rather than creating three independent implementations.

| Stage | Work | Relationship to original priorities | Exit |
| --- | --- | --- | --- |
| 0 | A1 compatibility/positioning; A6 partner brief; A5 bounded research | Prepare adoption without expanding connector scope | Clear contract and evidence requirements; AP2 research cannot block core work |
| 1 | U1 semantics/regressions across supported groups; U2/U3 shared contracts and refund reference; refund U4 | Host expertise (#2) enables Stripe deepening (#1) | Installed facade demo and real concurrent PostgreSQL refund conformance |
| 2 | U5 plus A2; U6; refund U7/U8; refund U9; minimal A3 and refund U10 docs | Recovery (#3) completes a usable refund integration | Sandbox-qualified refund path with restart recovery and inspectable evidence |
| 3 | A6 outside refund exercise; A4 if its channel is required | Validate #1–#3 together in an outside application | Recorded integration evidence and understood host/operator responsibilities |
| 4 | Remaining subscription/credit-note U2–U4, U7–U10 qualification and a second-action exercise | Finish #1/#2 depth using the proven common layers | All promised groups/dispositions qualified to their stated evidence level |
| 5 | Demand-led channel adapters, stronger signed evidence, possible AP2 adapter | Subsequent roadmap | Concrete adopter need and separately reviewed designs |

All implementation remains sequential. Shared contracts should accommodate the three existing typed protocols from the beginning, but billing-specific recipes and qualification need not block the first refund handoff. Supported billing defects and regression checks are still addressed immediately. If no outside host is available, record that limitation and continue stage 4; do not turn recruitment into an indefinite engineering wait.

This sequencing changes the unit of delivery from “finish ten features” to “prove one complete adoption path, then repeat it.” It preserves progressive customization: the simple entry point always compiles to the same execution machinery, and advanced host duties become visible as the developer replaces the supplied recipe.

## Review limitations

This is a source-grounded assessment and roadmap amendment, not a rerun of the reviewer's suite, a production audit, or a market-size study. No code, release metadata, public descriptions, provider resources, or external approval channels were changed. The 0.3.2 fixes described by the reviewer are already in the inspected source and should not be counted as new tasks.
