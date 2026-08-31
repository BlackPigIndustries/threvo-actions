# Supplier destination change

This reference application runs an initiator and a supplier-master receiver as
two local FastAPI services. It demonstrates why approving a destination change
is a different effect from later releasing a payment.

```bash
uv run pytest -q examples/supplier_destination/test_example.py
```

The example includes:

- confidential extracted bank details;
- separate initiator and receiver authentication boundaries;
- dual authority;
- request, tenant, audience, state, and replay binding at the receiver;
- authoritative verification of the receiver's state; and
- a later payment bound to the verified supplier-destination version.

Its `application/v0` transport envelope belongs only to the example. It is not
a proposed public protocol.

Start with
[`domain.py`](https://github.com/BlackPigIndustries/threvo-actions/blob/develop/examples/supplier_destination/domain.py),
then follow the
[`initiator_service.py`](https://github.com/BlackPigIndustries/threvo-actions/blob/develop/examples/supplier_destination/initiator_service.py)
and
[`receiver_service.py`](https://github.com/BlackPigIndustries/threvo-actions/blob/develop/examples/supplier_destination/receiver_service.py)
boundaries. The full failure matrix lives in
[`test_example.py`](https://github.com/BlackPigIndustries/threvo-actions/blob/develop/examples/supplier_destination/test_example.py).
