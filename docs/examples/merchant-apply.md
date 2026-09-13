# Merchant `apply_change`

The [source example](https://github.com/BlackPigIndustries/threvo-actions/tree/develop/examples/merchant_apply)
shows how an agentic merchant portal can keep its existing model-facing safety
gates and add durable controls at the application write boundary.

The mapping is intentionally small:

| `commerce-agents` value | Governed value |
| --- | --- |
| `StagedChange.change_id` | `ApplyChangeCommand.change_reference` and stable semantic-effect input |
| price item's `target` | host-resolved `listing_reference` |
| price item's `before` | committed private snapshot and catalog precondition |
| price item's `after` | approved target price |
| host approval mark | authenticated callback that creates bound `AuthorityEvidence` |
| `MerchantBackend.apply_change` | `ActionRuntime.execute` followed by independent verification |
| applied ledger status | derived host projection after verified completion |

The upstream guardrail remains useful. It limits the proposal before this
runtime sees it. The runtime then protects against state drift, competing
writers, replay and ambiguous target observations. Neither layer replaces the
other.

Run:

```bash
uv run pytest -q examples/merchant_apply/test_example.py
```

The test file is the executable proof for the three receipt paths. The example
does not claim to implement the full `MerchantBackend`; it demonstrates the
one consequential method where the two libraries compose.

The repository also includes `scripts/verify-commerce-agents-merchant-apply`.
It refuses any checkout except pinned commit `fd4d592`, installs the required
upstream packages in an ephemeral `uv` run, loads the official retail catalog,
stages a price change through `MockRetailMerchant`, and proves that
`check_apply_change` holds it until the host approval mark exists. It then maps
that fixture into the governed example and reaches verified completion with one
catalog write. This keeps the upstream gate in front without adding the external
project to this package's dependency graph.
