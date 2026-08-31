# Examples

All examples run locally, use fake external systems, and require no cloud
account or model API key.

| Example | Shows | Run |
| --- | --- | --- |
| Documentation quickstart | Full prepare → authority → execute → verify → read path | `uv run python -m examples.docs.quickstart` |
| Lifecycle edge cases | Drift, expiry, competing proposals, delayed verification, and erasure | `uv run python -m examples.docs.lifecycle_scenarios` |
| Pydantic AI agent | Offline agent tool, deferred authority, and verified result | `uv run --extra pydantic-ai python -m examples.docs.pydantic_ai_agent` |
| Pydantic AI across requests | Persist, approve, and resume a deferred agent action | `uv run --extra pydantic-ai python -m examples.docs.pydantic_ai_deferred` |
| PostgreSQL lifecycle | Durable prepare, authority, execution, verification, and scoped read | `DATABASE_URL=... uv run --extra postgres python -m examples.docs.postgres_runtime` |
| Custom-store conformance | Official SQLite adapter, explicit migration, and reusable store contract | `uv run python -m examples.docs.custom_store_conformance` |
| PSP refund | Timeout-after-acceptance recovery, stable target idempotency, and final-absence rules | `uv run pytest -q examples/refund/test_example.py` |
| Supplier destination change | Two FastAPI services, dual authority, receiver binding, and a later payment tied to the verified destination version | `uv run pytest -q examples/supplier_destination/test_example.py` |

The first two are designed for reading and copying. The reference applications
are deliberately larger: their tests demonstrate failure paths that a short
snippet would hide.
