from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

from threvo_actions import cli
from threvo_actions.migrations import MigrationStatus
from threvo_actions.mysql_migrations import MySQLMigrationStatus

if TYPE_CHECKING:
    from datetime import timedelta


class FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeMySQLPool:
    def __init__(self) -> None:
        self.closed = False
        self.waited = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.waited = True


def test_skill_path_prints_the_bundled_agent_skill(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["skill", "path"]) == 0

    skill_path = Path(capsys.readouterr().out.strip())
    assert skill_path.is_dir()
    assert (skill_path / "SKILL.md").is_file()
    assert (skill_path / "agents" / "openai.yaml").is_file()


@pytest.mark.parametrize("command", ["inspect", "migrate"])
def test_postgres_commands_use_named_environment_secret_and_close_pool(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pool = FakePool()
    created_with: list[str] = []
    calls: list[str] = []
    asyncpg = ModuleType("asyncpg")

    async def create_pool(dsn: str, *, min_size: int, max_size: int) -> FakePool:
        assert (min_size, max_size) == (1, 2)
        created_with.append(dsn)
        return pool

    async def inspect_postgres(unused_pool: object, *, schema: str) -> MigrationStatus:
        assert unused_pool is pool
        calls.append(f"inspect:{schema}")
        return MigrationStatus((), (1,))

    async def migrate_postgres(
        unused_pool: object,
        *,
        schema: str,
        lock_timeout: timedelta,
        writers_quiesced: bool,
    ) -> MigrationStatus:
        assert unused_pool is pool
        assert lock_timeout.total_seconds() == 12.0
        assert writers_quiesced
        calls.append(f"migrate:{schema}")
        return MigrationStatus((1,), ())

    asyncpg.__dict__["create_pool"] = create_pool
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg)
    monkeypatch.setattr("threvo_actions.migrations.inspect_postgres", inspect_postgres)
    monkeypatch.setattr("threvo_actions.migrations.migrate_postgres", migrate_postgres)
    monkeypatch.setenv("ACTIONS_TEST_DATABASE_URL", "postgresql://secret")

    argv = [
        "postgres",
        command,
        "--dsn-env",
        "ACTIONS_TEST_DATABASE_URL",
        "--schema",
        "actions_test",
    ]
    if command == "migrate":
        argv.extend(["--lock-timeout-seconds", "12", "--writers-quiesced"])
    assert cli.main(argv) == 0

    assert created_with == ["postgresql://secret"]
    assert calls == [f"{command}:actions_test"]
    assert pool.closed
    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == "actions_test"


def test_postgres_command_reports_missing_optional_driver(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setitem(sys.modules, "asyncpg", None)
    monkeypatch.setenv("ACTIONS_TEST_DATABASE_URL", "postgresql://secret")

    assert (
        cli.main(
            [
                "postgres",
                "inspect",
                "--dsn-env",
                "ACTIONS_TEST_DATABASE_URL",
            ]
        )
        == 2
    )
    assert "threvo-actions[postgres]" in capsys.readouterr().out


@pytest.mark.parametrize(("owns_proposals", "expected_exit"), [(True, 3), (False, 0)])
def test_postgres_inspect_checks_runtime_role_separation(
    owns_proposals: bool,
    expected_exit: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pool = FakePool()
    asyncpg = ModuleType("asyncpg")

    async def create_pool(dsn: str, *, min_size: int, max_size: int) -> FakePool:
        del dsn, min_size, max_size
        return pool

    async def inspect_postgres(unused_pool: object, *, schema: str) -> MigrationStatus:
        assert unused_pool is pool
        assert schema == "threvo_actions"
        return MigrationStatus(
            (1, 2, 3, 4),
            (),
            connected_role_owns_proposals=owns_proposals,
        )

    asyncpg.__dict__["create_pool"] = create_pool
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg)
    monkeypatch.setattr("threvo_actions.migrations.inspect_postgres", inspect_postgres)
    monkeypatch.setenv("ACTIONS_RUNTIME_DATABASE_URL", "postgresql://secret")

    result = cli.main(
        [
            "postgres",
            "inspect",
            "--dsn-env",
            "ACTIONS_RUNTIME_DATABASE_URL",
            "--require-separated-role",
        ]
    )

    assert result == expected_exit
    output = json.loads(capsys.readouterr().out)
    assert output["connected_role_owns_proposals"] is owns_proposals
    assert output["role_boundary_valid"] is (not owns_proposals)


def test_postgres_command_requires_the_named_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISSING_ACTIONS_DATABASE_URL", raising=False)

    with pytest.raises(SystemExit, match="2"):
        cli.main(
            [
                "postgres",
                "inspect",
                "--dsn-env",
                "MISSING_ACTIONS_DATABASE_URL",
            ]
        )


@pytest.mark.parametrize("command", ["inspect", "migrate"])
def test_mysql_commands_use_named_environment_secret_and_close_pool(
    command: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pool = FakeMySQLPool()
    created_with: list[dict[str, object]] = []
    calls: list[str] = []
    aiomysql = ModuleType("aiomysql")

    async def create_pool(**kwargs: object) -> FakeMySQLPool:
        created_with.append(kwargs)
        return pool

    async def inspect_mysql(unused_pool: object) -> MySQLMigrationStatus:
        assert unused_pool is pool
        calls.append("inspect")
        return MySQLMigrationStatus((), (1,), "8.4.11")

    async def migrate_mysql(
        unused_pool: object,
        *,
        lock_timeout: timedelta,
        writers_quiesced: bool,
    ) -> MySQLMigrationStatus:
        assert unused_pool is pool
        assert lock_timeout.total_seconds() == 12.0
        assert writers_quiesced
        calls.append("migrate")
        return MySQLMigrationStatus((1,), (), "8.4.11")

    aiomysql.__dict__["create_pool"] = create_pool
    monkeypatch.setitem(sys.modules, "aiomysql", aiomysql)
    monkeypatch.setattr("threvo_actions.mysql_migrations.inspect_mysql", inspect_mysql)
    monkeypatch.setattr("threvo_actions.mysql_migrations.migrate_mysql", migrate_mysql)
    monkeypatch.setenv(
        "ACTIONS_TEST_MYSQL_URL",
        "mysql://actions:p%40ss@db.internal:3307/actions",
    )
    argv = ["mysql", command, "--dsn-env", "ACTIONS_TEST_MYSQL_URL"]
    if command == "migrate":
        argv.extend(["--lock-timeout-seconds", "12", "--writers-quiesced"])

    assert cli.main(argv) == 0
    assert calls == [command]
    assert pool.closed and pool.waited
    assert created_with == [
        {
            "autocommit": False,
            "charset": "utf8mb4",
            "db": "actions",
            "host": "db.internal",
            "maxsize": 2,
            "minsize": 1,
            "password": "p@ss",
            "port": 3307,
            "user": "actions",
        }
    ]
    output = json.loads(capsys.readouterr().out)
    assert output["server_version"] == "8.4.11"


def test_mysql_command_reports_missing_optional_driver(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setitem(sys.modules, "aiomysql", None)
    monkeypatch.setenv("ACTIONS_TEST_MYSQL_URL", "mysql://actions:secret@localhost/actions")

    assert cli.main(["mysql", "inspect", "--dsn-env", "ACTIONS_TEST_MYSQL_URL"]) == 2
    assert "threvo-actions[mysql]" in capsys.readouterr().out


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_migration_lock_timeout_must_be_finite_and_positive(
    timeout: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACTIONS_TEST_DATABASE_URL", "postgresql://secret")

    with pytest.raises(SystemExit, match="2"):
        cli.main(
            [
                "postgres",
                "migrate",
                "--dsn-env",
                "ACTIONS_TEST_DATABASE_URL",
                "--lock-timeout-seconds",
                timeout,
            ]
        )
