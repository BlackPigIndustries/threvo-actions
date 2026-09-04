# Installation

`threvo-actions` supports Python 3.11, 3.12, and 3.13.

!!! note "Release availability"

    These commands install the immutable `0.1.5` release after the signed tag
    has completed the TestPyPI and PyPI release workflow. Never install a
    moving branch for a financial-action runtime.

=== "pip"

    ```bash
    python -m pip install "threvo-actions==0.1.5"
    ```

=== "uv"

    ```bash
    uv add "threvo-actions==0.1.5"
    ```

The core package depends only on Pydantic and the Python standard library.

## Optional integrations

=== "AWS KMS protection"

    Before the signed release completes, maintainers can install the reviewed
    source candidate with:

    ```bash
    uv sync --extra aws-kms --locked
    ```

    After the signed release completes, install the immutable package with:

    ```bash
    python -m pip install "threvo-actions[aws-kms]==0.1.5"
    ```

    The extra installs the AES-GCM dependency, not an AWS SDK. Adapt the host's
    existing async KMS client and durable wrapped-key store to the small typed
    ports in the [AWS KMS guide](../integrations/aws-kms.md).

=== "PostgreSQL"

    ```bash
    python -m pip install "threvo-actions[postgres]==0.1.5"
    ```

=== "MySQL"

    ```bash
    python -m pip install "threvo-actions[mysql]==0.1.5"
    ```

    Use the official MySQL 8 adapter for production-oriented multi-worker
    evaluation. See the [MySQL guide](../integrations/mysql.md).

=== "SQLAlchemy and Alembic"

    ```bash
    python -m pip install "threvo-actions[sqlalchemy]==0.1.5"
    ```

    The host-framework recipe uses SQLAlchemy for application business data,
    the qualified asyncpg action stores, and Alembic as deployment
    orchestration. See [SQLAlchemy and Alembic](../integrations/sqlalchemy-alembic.md).

=== "Pydantic AI"

    ```bash
    python -m pip install "threvo-actions[pydantic-ai]==0.1.5"
    ```

    The integration pins the Pydantic AI version it is tested against but does
    not install a model-provider SDK. Add the provider extra your application
    uses.

=== "SQLite"

    SQLite support uses the Python standard library, so the base installation
    already includes it:

    ```bash
    python -m pip install "threvo-actions==0.1.5"
    ```

    It is supported for local development, evaluation, tests, and bounded
    single-writer deployments. See the [SQLite guide](../integrations/sqlite.md).

=== "Documentation development"

    ```bash
    git clone https://github.com/BlackPigIndustries/threvo-actions.git
    cd threvo-actions
    uv sync --extra dev --extra mysql --extra pydantic-ai --extra postgres --extra docs
    uv run mkdocs serve
    ```

## Verify the installation

```bash
python - <<'PY'
from decimal import Decimal

from threvo_actions import Money

amount = Money(amount=Decimal("19.95"), currency="EUR")
print(amount.model_dump(mode="json"))
PY
```

Output:

```text
{'amount': '19.95', 'currency': 'EUR'}
```

The model is strict and immutable. It rejects a floating-point amount, a
lowercase currency, unexpected fields, or later mutation.

## Give your coding agent the library skill

The distribution includes an Agent Skills-compatible guide with the current
authoring API, safety boundaries, Pydantic AI wiring, and test matrix. Locate
the exact copy bundled with the installed package:

```bash
threvo-actions skill path
```

Install that directory with a compatible skill manager:

```bash
THREVO_ACTIONS_SKILL_DIR=$(threvo-actions skill path) || exit 1
npx skills add "$THREVO_ACTIONS_SKILL_DIR" \
  --skill threvo-actions --agent '*' --yes
```

This uses the skill that shipped with the Python version your application
installed. See [Coding agents](../integrations/coding-agents.md) for source,
project-local, and global installation options and the upgrade procedure.
