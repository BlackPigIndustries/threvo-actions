# Confirm-first authority

Confirmation is not a boolean attached to a tool call. It is evidence bound to
the exact proposal the application prepared.

`AuthorityEvidence` binds all of these dimensions:

- tenant;
- action name and version;
- proposal instance;
- host-defined semantic effect;
- keyed commitment to the private snapshot;
- confirming authority;
- required audience and channel assurance;
- decision, issue time, and expiry.

Changing any bound value makes the evidence invalid for that proposal.

## Record a decision

The host authenticates the confirmer, applies its own policy, reads the
proposal from trusted storage, constructs the bound evidence, and records it:

```python
--8<-- "examples/docs/quickstart.py:record-authority"
```

The example evidence requirement needs one specific manager:

```python
--8<-- "examples/docs/quickstart.py:authority-policy"
```

`SingleApproval`, `AnyApproval`, and `MOfNApprovals` count distinct, bound
approval evidence. They never decide whether an authority is currently allowed
to act. The host's `can_decide()` check remains mandatory and owns tenant roles,
revocation, delegation, amount limits, and segregation of duties.

The runtime calls the evaluator again immediately before execution with only
currently valid evidence. A repeated record from the same authority cannot fill
two seats in an M-of-N requirement.

## What does not count as authority

- model text saying “approved”;
- a browser field controlled by the client;
- Pydantic AI `ToolApproved`;
- copied conversation history;
- possession of a proposal reference;
- successful authentication without the required authorization policy.

Those values may route a continuation, but `execute()` still refuses to call
the executor until server-recorded evidence satisfies the host policy.

!!! tip "Hide unknown and unauthorized the same way"

    `record_authority()` and `read()` intentionally use `ProposalNotFoundError`
    for several authorization failures. Avoid giving callers a proposal
    enumeration oracle.
