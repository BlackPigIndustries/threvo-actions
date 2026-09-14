# External authority artifacts

Status: **minimized reference implemented in 0.6.0; format adapters remain
trigger-bound**.

Some hosts verify an AP2 mandate, SD-JWT, managed-agent interrupt, or another
signed record before they create `AuthorityEvidence`. The optional
`ExternalAuthorityAttestation` retains the minimum reference needed to bind
that host evidence to the action:

- `format`: exact host-understood format or profile;
- `issuer_reference`: opaque issuer or trust-domain reference;
- `artifact_reference`: opaque lookup handle to host custody; and
- `artifact_digest`: the host's algorithm-qualified content digest.

The raw token, disclosure, signature, and personal claims stay in host-owned
custody. The runtime does not parse the format or verify signatures. The block
is part of the persisted authority record and its evidence export, so replacing
the reference or digest changes the bundle digest. Validation detects that
internal inconsistency; it cannot establish that the artifact was valid or that
the exporter is authentic.

Hosts define issuer trust, audience, time, replay, revocation, retention, and
erasure. Destroying runtime proposal content cannot erase an issuer's or
verifier's copy. The evidence bundle therefore continues to say
`authenticity: unsigned_host_projection`.

Build a format-specific verifier only when a named adopter supplies a real
artifact profile and fixtures for valid, tampered, swapped, expired, revoked,
and cross-tenant cases. That adapter belongs behind host authority evaluation;
no protocol or signature dependency belongs in the core package.

An agent-identity attestation remains unimplemented. A real adopter must first
establish whether the proposal needs one or several artifacts, key rotation and
revocation state, and which minimized fields may enter authorized reads.
