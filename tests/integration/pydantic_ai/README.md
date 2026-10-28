# Pydantic AI integration tests

These tests drive `threvo_actions.integrations.pydantic_ai` through a real
Pydantic AI `Agent` and a deterministic `FunctionModel`, with model requests
disabled, so no test reaches a provider. They run against the version in
`uv.lock` (Pydantic AI 2.61.0 since 27/10/2026), not the lowest version the
`pydantic-ai` extra admits.

- `support.py` builds the fixed and scoped action stacks the tests share.
- `test_capability.py` and `test_scoped_capability.py` cover the capability's
  tools for a fixed tenant and for a scoped one.
- `test_tool_schema.py` covers the tool schema and dispatch, including that
  invalid arguments stay out of the retry the model sees. From Pydantic AI
  2.60 that retry reaches the model as `ToolReturnPart(outcome="retried")`, not
  `RetryPromptPart`.
- `test_deferred_authority.py` and `test_history_spoofing.py` cover deferred
  approval, and replayed or forged history that tries to supply authority.
- `test_recovery_tools.py` covers the recovery tools.
- `test_examples.py` runs the documented Pydantic AI examples.
