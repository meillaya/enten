from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import anyio
import asyncpg

from enten.migration_baseline import baseline_existing
from enten.migration_core import BaselineMismatchError, MigrationError
from enten.migration_operations import (
    apply_migrations,
    assert_local_downgrade,
    asyncpg_url,
    downgrade_migrations,
)


async def run_cli(args: argparse.Namespace) -> None:
    conn = await asyncpg.connect(asyncpg_url(args.database_url))
    try:
        match args.command:
            case "apply":
                if args.baseline_existing:
                    ledger = await conn.fetchval("SELECT to_regclass('public.schema_migrations')")
                    managed = await conn.fetchval("SELECT to_regclass('public.jobs')")
                    if ledger is None and managed is not None:
                        for candidate in (3, 2, 1):
                            try:
                                await baseline_existing(
                                    conn, args.migrations, through_version=candidate
                                )
                            except BaselineMismatchError:
                                continue
                            break
                        else:
                            raise BaselineMismatchError("schema matches no exact known baseline")
                applied = await apply_migrations(conn, args.migrations)
                print("applied:", ", ".join(item.filename for item in applied) or "none")
            case "baseline":
                applied = await baseline_existing(
                    conn, args.migrations, through_version=args.through_version
                )
                print("baselined:", ", ".join(item.filename for item in applied))
            case "downgrade":
                assert_local_downgrade(
                    args.database_url,
                    authorized=os.environ.get("ALLOW_LOCAL_DB_DOWNGRADE") == "1",
                )
                removed = await downgrade_migrations(
                    conn, args.migrations, target_version=args.target_version
                )
                print("removed:", ", ".join(str(version) for version in removed))
            case unreachable:
                raise AssertionError(f"unreachable command: {unreachable}")
    finally:
        await conn.close()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Checksum-verified Postgres migrations")
    result.add_argument(
        "--database-url", default=os.environ.get("DATABASE_URL", ""), required=False
    )
    result.add_argument("--migrations", type=Path, default=Path("migrations"))
    subparsers = result.add_subparsers(dest="command", required=True)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--baseline-existing", action="store_true")
    baseline = subparsers.add_parser("baseline")
    baseline.add_argument("--through-version", type=int, required=True)
    downgrade = subparsers.add_parser("downgrade")
    downgrade.add_argument("--target-version", type=int, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    if not args.database_url:
        print("DATABASE_URL or --database-url is required", file=sys.stderr)
        return 2
    try:
        anyio.run(run_cli, args)
    except MigrationError as exc:
        print(f"migration error: {exc}", file=sys.stderr)
        return 1
    return 0
