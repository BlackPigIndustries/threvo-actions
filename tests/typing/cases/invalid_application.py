from valid_application import (
    ActionApplication,
    ActionPorts,
    ActionRecipe,
    ActionSpec,
    Command,
    Dependencies,
    Preview,
    PrivateSnapshot,
    RegisteredAction,
    Result,
)

application = ActionApplication[Dependencies]()
specification = ActionSpec[Command, PrivateSnapshot, Preview, Result](
    command_model=Command,
    private_snapshot_model=PrivateSnapshot,
    preview_model=Preview,
    result_model=Result,
)
wrong_recipe = ActionRecipe[Dependencies, Command, PrivateSnapshot, Preview, Preview](
    bind=lambda dependencies: ActionPorts(marker=dependencies.tenant_reference)
)

application.register(specification, wrong_recipe)
erased_handle: RegisteredAction
