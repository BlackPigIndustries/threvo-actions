# Merchant `apply_change` with durable action controls

This example places the Anthropic `commerce-agents` merchant pattern in front
of the existing `threvo-actions` runtime. The upstream session still checks
provenance, current guardrails and its host approval mark. A successful gate
does not mutate the catalog. It creates or resumes a bound runtime proposal:

```text
stage_price_update -> runtime.prepare -> host approval -> record_authority
                   -> runtime.execute -> catalog CAS -> runtime.verify
```

The catalog marks the staged change applied only after its live write succeeds.
The runtime state comes from receipts and authoritative queries, so an
acknowledgement or ledger label cannot manufacture completion.

Run the local deterministic proof:

```bash
uv run pytest -q examples/merchant_apply/test_example.py
uv run python -m examples.merchant_apply.demo
```

| Case | Ungoverned apply risk | Runtime result |
| --- | --- | --- |
| Price changes after staging and approval | An old `before` value is overwritten | `STALE` / `material_drift`; zero catalog writes |
| Catalog accepts but its effect projection is not visible | An empty read is mistaken for failure and resent | `VERIFICATION_PENDING` / `PROVISIONAL_ABSENCE`; recovery says wait or reconcile and excludes execute |
| Two portal tabs approve proposals for the same change | Both call the writer | One effect claim reaches the catalog; the losing proposal is `REPLAYED` |

## Upstream compatibility

The comparison is pinned to
[`commerce-agents` commit `fd4d592`](https://github.com/anthropics/commerce-agents/commit/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf),
observed 13 September 2026. At that revision, `check_apply_change` validates
session provenance, re-runs `check_guardrails`, and checks the host approval
mark before it calls `MerchantBackend.apply_change`. Keep that gate. Replace
the backend's final write with this example's prepare/authority/execute/verify
mapping.

The local models deliberately do not import or vendor upstream types. The
checked [mapping document](../../docs/examples/merchant-apply.md) records the
field conversion and a reproducible upstream gate probe. Re-pin and re-run that
probe before adopting a newer upstream revision.

```bash
git clone https://github.com/anthropics/commerce-agents.git /tmp/commerce-agents
git -C /tmp/commerce-agents checkout fd4d59224ab96b43c6dc6888207c67b3bd5a24cf
./scripts/verify-commerce-agents-merchant-apply /tmp/commerce-agents
```

The example uses process-local storage and `EphemeralProtection`. Those are
test fixtures. A deployment replaces them with a qualified durable store,
managed protection, authenticated principals, its normal atomic catalog writer
and a durable reconciliation worker.
