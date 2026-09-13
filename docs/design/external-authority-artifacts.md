# External authority artifact design

Status: **designed; implementation held pending adopter evidence**.

Some hosts verify an AP2 mandate, SD-JWT, managed-agent interrupt, or another
signed record before they create `AuthorityEvidence`. The current runtime can
consume the host's authorization decision, but it cannot retain a minimized
reference to that external artifact. Adding one prematurely would put an
unproven schema on the authority hot path during the 0.4 stabilization hold.

## Proposed boundary

A future `ExternalAuthorityArtifact` should be a strict, frozen Pydantic model
and an optional member of `AuthorityEvidence`. It should carry only:

- `format_reference`: exact host-understood format/profile;
- `issuer_reference`: opaque issuer or trust-domain reference;
- `artifact_reference`: opaque lookup handle to host custody;
- `artifact_digest_algorithm` and `artifact_digest`;
- optional `verifier_reference`, `verifier_revision`, and `verified_at` when
  the host asserts that verification occurred.

The raw token, disclosure, signature and personal claims remain in host-owned
custody. The runtime neither parses the format nor verifies signatures. The
artifact block becomes part of the recorded authority representation so a
later substitution changes the evidence digest and cannot preserve the same
binding.

One block may prove insufficient. Real adoption must determine whether a
decision needs several artifacts, an issuer chain, verifier-key revision,
revocation state, or supersession. That is why the implementation cannot assume
a single `external_attestation` field today.

The same constraint applies to a proposing agent. Visa TAP key identifiers or
another network's agent-token binding may be useful receipt evidence, but an
optional `ProposingAgent.attestation_reference` would be too narrow without a
real host. The adopter must first establish whether the proposal needs one or
several artifacts, the verified agent principal, issuer and verifier revisions,
rotation/revocation state, and which minimized fields may enter reads and
exports. Design that projection with the authority artifact rather than adding
an isolated opaque string to every proposal now.

## Evidence export

The public evidence bundle may expose the minimized artifact references and
digests only when the evidence-read policy permits it. Validation can detect an
internal digest or reference inconsistency. It cannot prove that the artifact
was valid, that the verifier ran, or that the exporter is authentic. The bundle
must retain `authenticity: unsigned_host_projection` until a separately reviewed
signature and custody design changes that claim.

Hosts must define retention and erasure for the raw artifact, verifier logs,
exported references and backups. Destroying runtime proposal content cannot
erase an issuer's or verifier's copy.

## Implementation trigger and qualification

Implement only when a named adopter supplies one real artifact format and its:

1. exact schema/profile and digest rules;
2. issuer, audience, time, replay and revocation policy;
3. verifier identity and versioning model;
4. retention, erasure and evidence-export requirements; and
5. valid, tampered, swapped, expired, revoked and cross-tenant fixtures.

If the artifact identifies a proposing agent, qualification also covers agent
key rotation, a valid artifact bound to the wrong proposal, and retention in
proposal receipts and authorized evidence reads.

The first implementation must update authority persistence, all official store
migrations, canonical evidence tests, leakage tests, export validation,
versioning documentation and the bundled coding-agent guide in one change.
No cryptography or protocol dependency belongs in the core package.
