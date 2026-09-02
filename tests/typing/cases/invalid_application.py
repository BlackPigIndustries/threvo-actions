from valid_application import (
    ActionApplication,
    ActionRecipe,
    Command,
    Dependencies,
    Preview,
    PrivateSnapshot,
    RegisteredAction,
    bind_components,
    specification,
)

application = ActionApplication[Dependencies]()
wrong_recipe = ActionRecipe[Dependencies, Command, PrivateSnapshot, Preview, Preview](
    bind=lambda dependencies: bind_components(dependencies)
)

application.register(specification, wrong_recipe)
erased_handle: RegisteredAction
