# Boundary models

All library boundary models are strict, frozen Pydantic v2 models that forbid
extra fields. `Money` uses `Decimal` and requires an explicit uppercase
three-letter currency. It preserves three-decimal and other valid precisions;
the host validates the permitted minor units for its currency or payment rail.

::: threvo_actions.models
    options:
      members: true
      show_source: false
