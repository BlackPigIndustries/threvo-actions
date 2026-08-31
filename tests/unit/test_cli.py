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
from threvo_actions.readiness import (
    DatabaseAccessLane,
    DatabaseAdapter,
    DatabaseReadiness,
)

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


def test_postgres_plan_is_read_only_and_emits_pending_exact_sql(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pool = FakePool()
    asyncpg = ModuleType("asyncpg")

    async def create_pool(dsn: str, *, min_size: int, max_size: int) -> FakePool:
        assert dsn == "postgresql://secret"
        assert (min_size, max_size) == (1, 2)
        return pool

    async def inspect_postgres(unused_pool: object, *, schema: str) -> MigrationStatus:
        assert unused_pool is pool
        assert schema == "actions_test"
        return MigrationStatus((1, 2, 3), (4,))

    asyncpg.__dict__["create_pool"] = create_pool
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg)
    monkeypatch.setattr("threvo_actions.migrations.inspect_postgres", inspect_postgres)
    monkeypatch.setenv("ACTIONS_TEST_DATABASE_URL", "postgresql://secret")

    assert (
        cli.main(
            [
                "postgres",
                "plan",
                "--dsn-env",
                "ACTIONS_TEST_DATABASE_URL",
                "--schema",
                "actions_test",
            ]
        )
        == 0
    )

    output = json.loads(capsys.readouterr().out)
    assert output["applied_versions"] == [1, 2, 3]
    assert output["pending_versions"] == [4]
    assert output["migrations"][0]["phase"] == "contract"
    assert output["migrations"][0]["requires_writer_quiescence"] is True
    assert "__THREVO_ACTIONS_" not in output["migrations"][0]["sql"]
    assert pool.closed


def test_postgres_script_renders_without_database_credentials_or_driver(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setitem(sys.modules, "asyncpg", None)
    monkeypatch.delenv("ACTIONS_TEST_DATABASE_URL", raising=False)

    assert (
        cli.main(
            [
                "postgres",
                "script",
                "--all",
                "--schema",
                "actions_test",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert output.startswith("BEGIN;\n")
    assert 'CREATE SCHEMA IF NOT EXISTS "actions_test"' in output
    assert output.endswith("COMMIT;\n")


def test_postgres_script_requires_quiescence_for_contract_upgrade(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="2"):
        cli.main(
            [
                "postgres",
                "script",
                "--from-version",
                "2",
            ]
        )

    assert "stopped runtime and retention writers" in capsys.readouterr().err


def test_grant_commands_render_without_database_credentials_or_drivers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(
            [
                "postgres",
                "grants",
                "--schema",
                "actions",
                "--runtime-role",
                "runtime",
                "--retention-role",
                "retention",
            ]
        )
        == 0
    )
    postgres_sql = capsys.readouterr().out
    assert 'GRANT USAGE ON SCHEMA "actions" TO "runtime", "retention"' in postgres_sql

    assert (
        cli.main(
            [
                "mysql",
                "grants",
                "--database",
                "actions",
                "--runtime-user",
                "runtime",
                "--runtime-host",
                "10.%",
                "--retention-user",
                "retention",
                "--retention-host",
                "10.%",
            ]
        )
        == 0
    )
    mysql_sql = capsys.readouterr().out
    assert "GRANT EXECUTE ON PROCEDURE `actions`.`threvo_actions_create_proposal`" in mysql_sql
    assert "TO 'runtime'@'10.%'" in mysql_sql


def test_ready_commands_emit_machine_readable_status_and_exit_nonzero_when_unsafe(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    postgres_pool = FakePool()
    asyncpg = ModuleType("asyncpg")

    async def create_postgres_pool(dsn: str, *, min_size: int, max_size: int) -> FakePool:
        del dsn, min_size, max_size
        return postgres_pool

    async def check_postgres_readiness(
        unused_pool: object,
        *,
        schema: str,
        lane: DatabaseAccessLane,
    ) -> DatabaseReadiness:
        assert unused_pool is postgres_pool
        assert schema == "actions"
        assert lane is DatabaseAccessLane.RUNTIME
        return DatabaseReadiness(
            DatabaseAdapter.POSTGRESQL,
            lane,
            (1, 2, 3, 4),
            (),
            True,
            True,
            (),
        )

    asyncpg.__dict__["create_pool"] = create_postgres_pool
    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg)
    monkeypatch.setattr(
        "threvo_actions.migrations.check_postgres_readiness",
        check_postgres_readiness,
    )
    monkeypatch.setenv("ACTIONS_RUNTIME_DATABASE_URL", "postgresql://secret")

    assert (
        cli.main(
            [
                "postgres",
                "ready",
                "--dsn-env",
                "ACTIONS_RUNTIME_DATABASE_URL",
                "--schema",
                "actions",
                "--lane",
                "runtime",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["ready"] is True
    assert postgres_pool.closed

    mysql_pool = FakeMySQLPool()
    aiomysql = ModuleType("aiomysql")

    async def create_mysql_pool(**kwargs: object) -> FakeMySQLPool:
        del kwargs
        return mysql_pool

    async def check_mysql_readiness(
        unused_pool: object,
        *,
        lane: DatabaseAccessLane,
    ) -> DatabaseReadiness:
        assert unused_pool is mysql_pool
        assert lane is DatabaseAccessLane.RETENTION
        return DatabaseReadiness(
            DatabaseAdapter.MYSQL,
            lane,
            (1, 2),
            (),
            True,
            False,
            ("found 1 unexpected privilege statements",),
        )

    aiomysql.__dict__["create_pool"] = create_mysql_pool
    monkeypatch.setitem(sys.modules, "aiomysql", aiomysql)
    monkeypatch.setattr(
        "threvo_actions.mysql_migrations.check_mysql_readiness",
        check_mysql_readiness,
    )
    monkeypatch.setenv(
        "ACTIONS_RETENTION_MYSQL_URL",
        "mysql://retention:secret@localhost/actions",
    )

    assert (
        cli.main(
            [
                "mysql",
                "ready",
                "--dsn-env",
                "ACTIONS_RETENTION_MYSQL_URL",
                "--lane",
                "retention",
            ]
        )
        == 3
    )
    output = json.loads(capsys.readouterr().out)
    assert output["ready"] is False
    assert output["issues"] == ["found 1 unexpected privilege statements"]
    assert mysql_pool.closed and mysql_pool.waited


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
