# Testing helpers

`threvo_actions.testing` provides deterministic host infrastructure for unit
tests and runnable examples. None of these helpers grants authority.

```python
from datetime import UTC, datetime, timedelta

from threvo_actions import ActionRuntime, MemoryActionStore
from threvo_actions.testing import (
    EphemeralProtection,
    FixedClock,
    RecordingEventSink,
    SequentialIdentifiers,
)

clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
identifiers = SequentialIdentifiers()
events = RecordingEventSink()
protection = EphemeralProtection(acknowledge_data_loss=True)

runtime = ActionRuntime(
    store=MemoryActionStore(),
    clock=clock,
    identifiers=identifiers,
    event_sink=events,
)

clock.advance(timedelta(minutes=5))
assert identifiers.new("proposal") == "proposal:1"
```

## What each helper does

| Helper | Purpose |
| --- | --- |
| `FixedClock` | Returns a timezone-aware time and moves forward only when the test calls `advance()`. |
| `SequentialIdentifiers` | Produces predictable references such as `proposal:1`. Never expose them outside tests. |
| `RecordingEventSink` | Stores minimized `RuntimeEvent` values in emission order. |
| `EphemeralProtection` | Uses per-commitment random HMAC keys and process-local payload storage. |

`EphemeralProtection` is not encryption or production key custody. It has no
durability, rotation, backup, recovery, access-control boundary, or shared
multi-process state. Its explicit acknowledgement prevents accidental
zero-argument construction. It remembers the expected opaque payload metadata
in memory so tests fail when stored codec, key version, or ciphertext fields
change; the ciphertext remains random test data and never contains plaintext.

The complete [first action](../getting-started/first-action.md) uses
`FixedClock`, `SequentialIdentifiers`, and `EphemeralProtection`, and can be
copied and run without external services. `RecordingEventSink` is available
when a test needs to assert which minimized runtime events were emitted.
