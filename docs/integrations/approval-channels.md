# Authenticated approval requests

Version 0.4.1 includes an installable, transport-neutral reference for binding
an approval request to one exact proposal. It is available from
**threvo_actions.integrations.approval_channels** and does not require the
source repository's examples directory.

The package provides:

- strict ApprovalRequestBinding, ApprovalRequestView, ApprovalDecisionRecord,
  and ApprovalRequestRecord models;
- PostgresApprovalRequestStore with immutable bindings and first-write-wins
  decisions; and
- a checksummed migration plus explicit render and migrate functions.

Install the PostgreSQL dependency with:

    uv add "threvo-actions[postgres]==0.4.1"

Apply the migration in a serialized deployment step. Constructors perform no
I/O and never migrate automatically:

    from threvo_actions.integrations.approval_channels import (
        migrate_approval_postgres,
    )

    await migrate_approval_postgres(pool, schema="application_approvals")

## Server-owned binding

The host creates the request only after authenticating the requester and
loading the current proposal. It stores:

- tenant and proposal instance;
- semantic effect and action type;
- exact proposal commitment;
- intended authenticated authority;
- authority audience and channel assurance; and
- creation and expiry times.

The URL or message sent through email, chat, or another transport contains only
an opaque request reference. A callback supplies that reference plus approve or
reject. It must not supply tenant, authority, commitment, audience, assurance,
or effect fields.

Before recording authority, the host:

1. authenticates the person following the request;
2. loads the immutable binding by opaque reference;
3. verifies the authenticated identity is the intended authority;
4. reloads the proposal and compares its tenant, action, effect, commitment,
   audience, assurance, and expiry;
5. evaluates current approval rights; and
6. persists the exact decision and AuthorityEvidence before calling
   record_authority.

Repeated matching callbacks reuse the persisted evidence. Concurrent matching
callbacks converge on the first record. A contradictory callback is refused.
If the HTTP response is lost, retrying uses the same bound evidence.

## What the package does not do

The store does not authenticate users, send notifications, authorize decisions,
or execute an action. The host owns those responsibilities. A delivery provider
never becomes business authority and should receive only the opaque reference
and host URL.

The host must bind every read and decision to its authenticated tenant session,
protect references in transit, rate-limit endpoints, define approver-revocation
behavior, and record access under its audit policy.

The source repository's Stripe refund application demonstrates the complete
HTTP wiring. Its bearer tokens are sandbox credentials. The application imports
the same installed models and PostgreSQL store that adopters receive from the
wheel.
