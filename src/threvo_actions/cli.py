"""Command-line entry points for explicit adapter administration."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import ssl
from datetime import timedelta
from importlib.resources import files
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, unquote, urlsplit

from .readiness import DatabaseAccessLane, DatabaseReadiness

if TYPE_CHECKING:
    from collections.abc import Sequence


def _positive_seconds(value: str) -> float:
    seconds = float(value)
    if not isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be finite and positive")
    return seconds


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="threvo-actions")
    commands = parser.add_subparsers(dest="command", required=True)
    skill = commands.add_parser("skill", help="locate bundled coding-agent guidance")
    skill_commands = skill.add_subparsers(dest="skill_command", required=True)
    skill_commands.add_parser("path", help="print the bundled Agent Skill directory")
    postgres = commands.add_parser("postgres", help="manage the PostgreSQL adapter schema")
    postgres_commands = postgres.add_subparsers(dest="postgres_command", required=True)
    for name in ("inspect", "plan", "ready", "migrate"):
        command = postgres_commands.add_parser(name)
        command.add_argument("--dsn-env", required=True, metavar="NAME")
        command.add_argument("--schema", default="threvo_actions")
        if name == "inspect":
            command.add_argument("--require-separated-role", action="store_true")
        if name == "ready":
            command.add_argument(
                "--lane",
                choices=tuple(lane.value for lane in DatabaseAccessLane),
                required=True,
            )
        if name == "migrate":
            command.add_argument("--writers-quiesced", action="store_true")
            command.add_argument(
                "--lock-timeout-seconds",
                type=_positive_seconds,
                default=30.0,
            )
    postgres_grants = postgres_commands.add_parser("grants")
    postgres_grants.add_argument("--schema", default="threvo_actions")
    postgres_grants.add_argument("--runtime-role", required=True)
    postgres_grants.add_argument("--retention-role", required=True)
    sqlite = commands.add_parser(
        "sqlite",
        help="manage a bounded-use SQLite adapter database",
    )
    sqlite_commands = sqlite.add_subparsers(dest="sqlite_command", required=True)
    for name in ("inspect", "migrate"):
        command = sqlite_commands.add_parser(name)
        command.add_argument("--database", required=True, metavar="PATH")
        if name == "migrate":
            command.add_argument(
                "--lock-timeout-seconds",
                type=_positive_seconds,
                default=30.0,
            )
    mysql = commands.add_parser("mysql", help="manage the MySQL 8 adapter schema")
    mysql_commands = mysql.add_subparsers(dest="mysql_command", required=True)
    for name in ("inspect", "ready", "migrate"):
        command = mysql_commands.add_parser(name)
        command.add_argument("--dsn-env", required=True, metavar="NAME")
        if name == "ready":
            command.add_argument(
                "--lane",
                choices=tuple(lane.value for lane in DatabaseAccessLane),
                required=True,
            )
        if name == "migrate":
            command.add_argument("--writers-quiesced", action="store_true")
            command.add_argument(
                "--lock-timeout-seconds",
                type=_positive_seconds,
                default=30.0,
            )
    mysql_grants = mysql_commands.add_parser("grants")
    mysql_grants.add_argument("--database", required=True)
    mysql_grants.add_argument("--runtime-user", required=True)
    mysql_grants.add_argument("--runtime-host", required=True)
    mysql_grants.add_argument("--retention-user", required=True)
    mysql_grants.add_argument("--retention-host", required=True)
    return parser


def _bundled_skill_path() -> Path:
    packaged = Path(str(files("threvo_actions").joinpath(".agents", "skills", "threvo-actions")))
    if packaged.is_dir():
        return packaged.resolve()

    checkout = Path(__file__).resolve().parents[2] / ".agents" / "skills" / "threvo-actions"
    if checkout.is_dir():
        return checkout.resolve()

    raise FileNotFoundError("the threvo-actions Agent Skill is missing from this installation")


async def _postgres(
    *,
    command: str,
    dsn: str,
    schema: str,
    lock_timeout_seconds: float,
    require_separated_role: bool,
    writers_quiesced: bool,
    lane: DatabaseAccessLane | None,
) -> int:
    try:
        import asyncpg
    except ModuleNotFoundError:
        print("PostgreSQL commands require: pip install 'threvo-actions[postgres]'")
        return 2

    from .migrations import (
        check_postgres_readiness,
        inspect_postgres,
        migrate_postgres,
        plan_postgres_migrations,
    )

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        if command == "plan":
            status = await inspect_postgres(pool, schema=schema)
            plan = plan_postgres_migrations(
                schema=schema,
                pending_versions=status.pending_versions,
            )
            print(
                json.dumps(
                    {
                        "applied_versions": status.applied_versions,
                        "migrations": [
                            {
                                "checksum": migration.checksum,
                                "compatible_with_previous_runtime": (
                                    migration.compatible_with_previous_runtime
                                ),
                                "filename": migration.filename,
                                "phase": migration.phase,
                                "requires_writer_quiescence": (
                                    migration.requires_writer_quiescence
                                ),
                                "sql": migration.sql,
                                "version": migration.version,
                            }
                            for migration in plan
                        ],
                        "pending_versions": status.pending_versions,
                        "schema": schema,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            return 0
        if command == "ready":
            if lane is None:
                raise AssertionError("ready command requires an access lane")
            readiness = await check_postgres_readiness(pool, schema=schema, lane=lane)
            _print_readiness(readiness)
            return 0 if readiness.ready else 3
        status = (
            await migrate_postgres(
                pool,
                schema=schema,
                lock_timeout=timedelta(seconds=lock_timeout_seconds),
                writers_quiesced=writers_quiesced,
            )
            if command == "migrate"
            else await inspect_postgres(pool, schema=schema)
        )
    finally:
        await pool.close()
    output: dict[str, object] = {
        "applied_versions": status.applied_versions,
        "connected_role_owns_proposals": status.connected_role_owns_proposals,
        "pending_versions": status.pending_versions,
        "schema": schema,
    }
    if status.connected_role_owns_proposals is True:
        output["security_warning"] = (
            "the connected role owns proposals; do not use this DSN in an application process"
        )
    if require_separated_role:
        boundary_valid = status.connected_role_owns_proposals is False
        output["role_boundary_valid"] = boundary_valid
    print(json.dumps(output, separators=(",", ":"), sort_keys=True))
    return 0 if not require_separated_role or boundary_valid else 3


async def _sqlite(
    *,
    command: str,
    database: str,
    lock_timeout_seconds: float,
) -> int:
    from .sqlite_migrations import inspect_sqlite, migrate_sqlite

    status = (
        await migrate_sqlite(
            database,
            lock_timeout=timedelta(seconds=lock_timeout_seconds),
        )
        if command == "migrate"
        else await inspect_sqlite(database)
    )
    print(
        json.dumps(
            {
                "applied_versions": status.applied_versions,
                "database": database,
                "pending_versions": status.pending_versions,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _mysql_dsn(dsn: str) -> dict[str, object]:
    parsed = urlsplit(dsn)
    database = parsed.path.removeprefix("/")
    query = parse_qs(parsed.query, strict_parsing=True)
    unexpected = set(query) - {"ssl_ca", "ssl_cert", "ssl_key"}
    if (
        parsed.scheme not in {"mysql", "mysql+aiomysql"}
        or parsed.hostname is None
        or parsed.username is None
        or not database
        or unexpected
        or any(len(values) != 1 for values in query.values())
    ):
        raise ValueError(
            "MySQL DSN must be mysql://user:password@host:port/database "
            "with only ssl_ca, ssl_cert, and ssl_key query options"
        )
    connection: dict[str, object] = {
        "host": parsed.hostname,
        "port": parsed.port or 3306,
        "user": unquote(parsed.username),
        "password": unquote(parsed.password or ""),
        "db": unquote(database),
        "autocommit": False,
        "charset": "utf8mb4",
    }
    certificate = query.get("ssl_cert")
    private_key = query.get("ssl_key")
    if (certificate is None) is not (private_key is None):
        raise ValueError("MySQL ssl_cert and ssl_key must be provided together")
    certificate_authority = query.get("ssl_ca")
    if certificate_authority is not None or certificate is not None:
        context = ssl.create_default_context(
            cafile=certificate_authority[0] if certificate_authority else None
        )
        if certificate is not None and private_key is not None:
            context.load_cert_chain(certificate[0], private_key[0])
        connection["ssl"] = context
    return connection


async def _mysql(
    *,
    command: str,
    dsn: str,
    lock_timeout_seconds: float,
    writers_quiesced: bool,
    lane: DatabaseAccessLane | None,
) -> int:
    try:
        import aiomysql
    except ModuleNotFoundError:
        print("MySQL commands require: pip install 'threvo-actions[mysql]'")
        return 2

    from .mysql_migrations import check_mysql_readiness, inspect_mysql, migrate_mysql

    try:
        connection = _mysql_dsn(dsn)
    except ValueError as exc:
        print(str(exc))
        return 2
    pool = await aiomysql.create_pool(minsize=1, maxsize=2, **connection)
    try:
        if command == "ready":
            if lane is None:
                raise AssertionError("ready command requires an access lane")
            readiness = await check_mysql_readiness(pool, lane=lane)
            _print_readiness(readiness)
            return 0 if readiness.ready else 3
        status = (
            await migrate_mysql(
                pool,
                lock_timeout=timedelta(seconds=lock_timeout_seconds),
                writers_quiesced=writers_quiesced,
            )
            if command == "migrate"
            else await inspect_mysql(pool)
        )
    finally:
        pool.close()
        await pool.wait_closed()
    print(
        json.dumps(
            {
                "applied_versions": status.applied_versions,
                "pending_versions": status.pending_versions,
                "server_version": status.server_version,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


def _print_readiness(readiness: DatabaseReadiness) -> None:
    print(
        json.dumps(
            {
                "adapter": readiness.adapter,
                "applied_versions": readiness.applied_versions,
                "issues": readiness.issues,
                "lane": readiness.lane,
                "pending_versions": readiness.pending_versions,
                "privilege_boundary_valid": readiness.privilege_boundary_valid,
                "ready": readiness.ready,
                "schema_current": readiness.schema_current,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "skill" and args.skill_command == "path":
        try:
            print(_bundled_skill_path())
        except FileNotFoundError as exc:
            parser.error(str(exc))
        return 0
    if args.command == "postgres":
        if args.postgres_command == "grants":
            from .migrations import render_postgres_grants

            try:
                grants = render_postgres_grants(
                    schema=args.schema,
                    runtime_role=args.runtime_role,
                    retention_role=args.retention_role,
                )
            except ValueError as exc:
                parser.error(str(exc))
            print(grants, end="")
            return 0
        dsn = os.environ.get(args.dsn_env)
        if dsn is None:
            parser.error(f"environment variable {args.dsn_env!r} is not set")
        return asyncio.run(
            _postgres(
                command=args.postgres_command,
                dsn=dsn,
                schema=args.schema,
                lock_timeout_seconds=getattr(args, "lock_timeout_seconds", 30.0),
                require_separated_role=getattr(args, "require_separated_role", False),
                writers_quiesced=getattr(args, "writers_quiesced", False),
                lane=(
                    DatabaseAccessLane(args.lane)
                    if getattr(args, "lane", None) is not None
                    else None
                ),
            )
        )
    if args.command == "sqlite":
        return asyncio.run(
            _sqlite(
                command=args.sqlite_command,
                database=args.database,
                lock_timeout_seconds=getattr(args, "lock_timeout_seconds", 30.0),
            )
        )
    if args.command == "mysql":
        if args.mysql_command == "grants":
            from .mysql_migrations import render_mysql_grants

            try:
                grants = render_mysql_grants(
                    database=args.database,
                    runtime_user=args.runtime_user,
                    runtime_host=args.runtime_host,
                    retention_user=args.retention_user,
                    retention_host=args.retention_host,
                )
            except ValueError as exc:
                parser.error(str(exc))
            print(grants, end="")
            return 0
        dsn = os.environ.get(args.dsn_env)
        if dsn is None:
            parser.error(f"environment variable {args.dsn_env!r} is not set")
        return asyncio.run(
            _mysql(
                command=args.mysql_command,
                dsn=dsn,
                lock_timeout_seconds=getattr(args, "lock_timeout_seconds", 30.0),
                writers_quiesced=getattr(args, "writers_quiesced", False),
                lane=(
                    DatabaseAccessLane(args.lane)
                    if getattr(args, "lane", None) is not None
                    else None
                ),
            )
        )
    raise AssertionError("unreachable command")
