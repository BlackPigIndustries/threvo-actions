# Runtime

`ActionRuntime` coordinates the lifecycle. Its public methods are async and
always require an explicit typed action definition.

::: threvo_actions.runtime
    options:
      members:
        - ActionRuntime
        - ActionOperationResult
        - ProposalView
        - OperationOutcome
        - RuntimeReasonCode
        - Clock
        - IdentifierProvider
        - SystemClock
        - UuidIdentifiers
        - ProposalNotFoundError
        - AuthorizationDeniedError
        - InvalidAuthorityEvidenceError
        - InvalidActionResultError
        - RetentionStoreUnavailableError
      show_source: false
