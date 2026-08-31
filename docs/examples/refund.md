# Refund reference application

The refund application models a PSP call where the network can fail after the
provider accepts the refund. It proves that the application verifies the
provider state instead of sending a second refund blindly.

```bash
uv run pytest -q examples/refund/test_example.py
```

Read these files in order:

1. [`domain.py`](https://github.com/BlackPigIndustries/threvo-actions/blob/develop/examples/refund/domain.py) — typed command, snapshot, preview, and result.
2. [`fake_psp.py`](https://github.com/BlackPigIndustries/threvo-actions/blob/develop/examples/refund/fake_psp.py) — target idempotency and authoritative query behavior.
3. [`app.py`](https://github.com/BlackPigIndustries/threvo-actions/blob/develop/examples/refund/app.py) — action ports and runtime wiring.
4. [`test_example.py`](https://github.com/BlackPigIndustries/threvo-actions/blob/develop/examples/refund/test_example.py) — executable normal and adversarial cases.

The tests cover stable per-intent PSP idempotency, atomic live-balance
reservation, material drift, timeout after acceptance, provisional versus
final absence, exact returned-effect binding, and authoritative completion.
