# Coding agents

`threvo-actions` ships an [Agent Skills](https://agentskills.io/)-compatible
skill. It teaches a coding agent how to author an action, preserve the host's
security boundaries, connect Pydantic AI, and test failure cases without
relying on remembered APIs.

The skill is guidance, not runtime authority. It cannot approve an action,
access private snapshots, or change what the Python package executes.

## Install from the source repository

Use this when you want the latest skill from the `develop` branch:

```bash
npx skills add BlackPigIndustries/threvo-actions \
  --skill threvo-actions --agent '*' --yes
```

The installer discovers `.agents/skills/threvo-actions/SKILL.md` and links or
copies it into the directories used by the selected coding agents. Remove
both `--agent '*'` and `--yes` to choose agents interactively. Without
`--global`, the installer writes to the current project; add `--global` for a
user-level installation.

## Install the skill matching your Python package

For an application that installs a wheel or source distribution, prefer the
skill bundled with that exact installation:

```bash
python -m pip install "threvo-actions==0.1.4"
THREVO_ACTIONS_SKILL_DIR=$(threvo-actions skill path) || exit 1
npx skills add "$THREVO_ACTIONS_SKILL_DIR" \
  --skill threvo-actions --agent '*' --yes
```

Keep the two-step path lookup: if the bundled skill is missing, the shell stops
before invoking the external installer.

`threvo-actions skill path` prints an absolute directory. It works from a
normal wheel installation, an installation built from the source distribution,
and a library checkout.

To inspect before installing:

```bash
THREVO_ACTIONS_SKILL_DIR=$(threvo-actions skill path) || exit 1
npx skills add "$THREVO_ACTIONS_SKILL_DIR" --list
```

## Use it

Ask the agent to use `$threvo-actions` when implementing or reviewing an
integration. For example:

```text
Use $threvo-actions to add a confirm-first refund action. Keep our existing
refund service as the only mutation path and treat the PSP query as the
authoritative verifier.
```

The skill routes the agent to four focused references:

- action authoring with `Action` and strict Pydantic boundary models;
- host integration, PostgreSQL roles, recovery, and migration order;
- Pydantic AI capability and deferred continuation wiring; and
- deterministic helpers, conformance checks, and adversarial failure tests.

## Keep it current

The skill metadata version matches the Python package version. CI validates
its schema, `SKILL.md` relative links, helper imports, and byte-for-byte
inclusion in the wheel and source distribution. A clean-install smoke test
proves the packaged copy remains readable through `threvo-actions skill path`.

Re-run the install command after upgrading `threvo-actions`. A skill manager
may copy the directory instead of linking it, and a copied skill does not
update when the Python package changes.

The installed Python API remains the final contract. The skill explicitly
tells agents to inspect local signatures and pinned versions before writing
code. See [Versioning](../versioning.md) for the supported and experimental
surfaces.
