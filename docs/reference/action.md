# Action authoring and approval requirements

For new integrations, start with the namespaced gradual-reveal API. It keeps
immutable semantics separate from operation-scoped host dependencies and
compiles to the same `ActionDefinition` and `ActionRuntime` used by the expert
path.

::: threvo_actions.experimental
    options:
      members:
        - ActionApplication
        - ActionApplicationError
        - ActionComponents
        - ActionIssueCode
        - ActionRecipe
        - ActionSpec
        - BoundAction
        - RegisteredAction
      show_source: false

`Action` remains the supported authoring facade for a host object that already
implements every port. The runtime continues to accept definitions only.

::: threvo_actions.action
    options:
      members:
        - Action
        - ActionConfigurationError
      show_source: false

Approval requirements evaluate evidence after the host has authenticated and
authorized each decision. They are not authorization policies.

::: threvo_actions.approvals
    options:
      members:
        - SingleApproval
        - AnyApproval
        - MOfNApprovals
        - ApprovalReasonCode
      show_source: false
