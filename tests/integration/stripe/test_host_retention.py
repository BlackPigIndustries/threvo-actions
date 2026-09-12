from __future__ import annotations

from pathlib import Path


def test_reference_schema_guards_reserved_customer_resources_and_retains_intents() -> None:
    sql = Path("examples/stripe_host/schema.sql").read_text(encoding="utf-8")

    assert "guard_reserved_customer_resource" in sql
    assert "FROM stripe_host_app.customer_resources" in sql
    assert "phase = 'reserved'" in sql
    assert "DELETE FROM threvo_stripe.intents" not in sql
