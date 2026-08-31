# Lifecycle edge cases

This program exercises the non-happy paths most integrations need to handle:

```bash
uv run python -m examples.docs.lifecycle_scenarios
```

Output:

```text
drift: stale, executor calls: 0
expiry: expired
competing proposal: replayed, executor calls: 1
verification: verification_pending -> verified
erasure: erased, content hidden: True
```

The program imports the complete [quickstart action](../getting-started/first-action.md)
and changes only the state necessary for each scenario.

```python
--8<-- "examples/docs/lifecycle_scenarios.py"
```
