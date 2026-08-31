# Action authoring and approval requirements

`Action` is an authoring facade. It compiles to `ActionDefinition`; the runtime
continues to accept definitions only.

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
