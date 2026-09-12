# Evidence exports

`ActionRuntime.export_evidence()` creates an authorized, minimized projection
of one stored proposal revision. Stripe action groups expose the same operation
through their `export_evidence(...)` methods.

```python
from threvo_actions import ActionEvidenceBundle, validate_evidence_bundle

bundle = await actions.refunds.export_evidence(
    proposal_reference,
    context=read_context,
)

report = validate_evidence_bundle(bundle)
assert report.digest_matches
```

The action's `can_read()` check runs before any evidence is returned. Unknown,
wrong-action, cross-tenant, and unauthorized references use the same
`ProposalNotFoundError` boundary as ordinary reads. An erased proposal exports
only its tombstone identity and an explicit `erased_source_evidence` omission;
destroyed evidence is never reconstructed.

`ActionEvidenceBundle`, `EvidenceValidationReport`,
`validate_evidence_bundle`, and `render_evidence_html` are available from the
package root in 0.4.1. The complete supporting vocabulary remains in
`threvo_actions.evidence`.

## Version contract

`threvo.actions.evidence/v1` is an immutable external document contract.
Changing a required field, field meaning, discriminator, canonical digest
input, or accepted value requires a new schema version and a separate model.
Producers do not add fields to v1 because strict v1 consumers reject unknown
fields. Readers that need to support more than one version must select the
matching model from the schema version before validation.

Evidence models use the neutral strict `ActionModel` Pydantic base. The base
supplies `extra="forbid"`, strict validation and frozen values; stability comes
from the version contract above rather than from a base-class name.

## Envelope contents

`threvo.actions.evidence/v1` includes:

- the proposal, action, and semantic-effect references;
- the exact source revision and lifecycle;
- the authorized display preview and safe result;
- ordered typed receipts and their embedded schema versions;
- minimized, non-replayable authority summaries;
- explicit omissions; and
- a SHA-256 content digest.

The preview and result use `FrozenJsonObject`. It stores every nested JSON value
as canonical immutable text internally and serializes as an ordinary JSON
object. Mutating the caller's original dictionary cannot change an export.

The export never includes the tenant, private snapshot, protected payload,
commitment, custody key, provider credentials, or a replayable
`AuthorityEvidence` envelope. Historical fields that were never recorded are
marked unavailable. They are not inferred from the current action policy.

## Validation and authenticity

`validate_evidence_bundle()` is pure and needs no optional dependency. It
checks the content digest, receipt identities and links, embedded versions,
and lifecycle evidence. Its result is one of:

- `consistent`: the supported fields agree internally;
- `inconsistent`: at least one supported invariant fails; or
- `insufficient`: retained evidence is missing, erased, or lacks legacy
  attribution.

A consistent result does **not** authenticate the exporter or prove a complete
history. The envelope is explicitly `unsigned_host_projection`. Anyone who can
rewrite an unsigned bundle can also recompute its digest. Detecting that change
requires comparing with a digest obtained through a separately trusted route.
The export does not claim an approval was legally sufficient, provide an
independent audit opinion, or establish compliance.

`render_evidence_html()` creates a self-contained escaped human view without
remote assets. The Stripe reference host can attach late observations with
`examples.stripe_host.evidence.attach_recovery_case()`. Those observations
remain separately attributed host case evidence and never become runtime
receipts or rewrite the source revision.
