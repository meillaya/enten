from __future__ import annotations

from pathlib import Path

import asyncpg

from enten.migration_baseline_spec import (
    BASELINE_COLUMNS,
    BASELINE_CONSTRAINTS,
    BASELINE_INDEXES,
    Catalog,
)
from enten.migration_core import (
    LEDGER_SQL,
    LOCK_KEY,
    BaselineMismatchError,
    Migration,
    discover_migrations,
)


def _expected_catalog(
    catalog: Catalog,
    through_version: int,
) -> dict[tuple[str, str], frozenset[str]]:
    expected: dict[tuple[str, str], frozenset[str]] = {}
    for version in range(1, through_version + 1):
        for relation, names in catalog.get(version, {}).items():
            expected[relation] = expected.get(relation, frozenset()) | names
    return expected


async def _catalog_names(
    conn: asyncpg.Connection,
    query: str,
    schema: str,
    table: str,
) -> frozenset[str]:
    return frozenset(await conn.fetchval(query, schema, table) or [])


async def assert_exact_baseline(conn: asyncpg.Connection, through_version: int) -> None:
    expected_columns = _expected_catalog(BASELINE_COLUMNS, through_version)
    expected_constraints = _expected_catalog(BASELINE_CONSTRAINTS, through_version)
    expected_indexes = _expected_catalog(BASELINE_INDEXES, through_version)
    for schema_table, columns in expected_columns.items():
        schema, table = schema_table
        actual_columns = await _catalog_names(
            conn,
            """
            SELECT coalesce(array_agg(column_name ORDER BY ordinal_position), ARRAY[]::text[])
            FROM information_schema.columns
            WHERE table_schema = $1 AND table_name = $2
            """,
            schema,
            table,
        )
        if actual_columns != columns:
            raise BaselineMismatchError(
                f"baseline mismatch for {schema}.{table}: expected {sorted(columns)}, "
                f"found {sorted(actual_columns)}"
            )
        actual_constraints = await _catalog_names(
            conn,
            """
            SELECT coalesce(array_agg(conname ORDER BY conname), ARRAY[]::text[])
            FROM pg_constraint AS catalog_constraint
            JOIN pg_class AS relation ON relation.oid = catalog_constraint.conrelid
            JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
            WHERE namespace.nspname = $1 AND relation.relname = $2
              AND catalog_constraint.contype <> 'n'
            """,
            schema,
            table,
        )
        if actual_constraints != expected_constraints[schema_table]:
            raise BaselineMismatchError(
                f"baseline constraints mismatch for {schema}.{table}: "
                f"expected {sorted(expected_constraints[schema_table])}, "
                f"found {sorted(actual_constraints)}"
            )
        actual_indexes = await _catalog_names(
            conn,
            """
            SELECT coalesce(array_agg(indexname ORDER BY indexname), ARRAY[]::text[])
            FROM pg_indexes
            WHERE schemaname = $1 AND tablename = $2
            """,
            schema,
            table,
        )
        if actual_indexes != expected_indexes[schema_table]:
            raise BaselineMismatchError(
                f"baseline indexes mismatch for {schema}.{table}: "
                f"expected {sorted(expected_indexes[schema_table])}, "
                f"found {sorted(actual_indexes)}"
            )
    vector_installed = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')"
    )
    if not vector_installed:
        raise BaselineMismatchError("baseline is missing the vector extension")
    later_relations = {
        relation
        for version, specs in BASELINE_COLUMNS.items()
        if version > through_version
        for relation in specs
        if relation not in expected_columns
    }
    for schema, table in later_relations:
        exists = await conn.fetchval("SELECT to_regclass($1)", f"{schema}.{table}")
        if exists is not None:
            raise BaselineMismatchError(f"baseline contains later relation {schema}.{table}")


async def baseline_existing(
    conn: asyncpg.Connection,
    path: Path,
    *,
    through_version: int,
) -> tuple[Migration, ...]:
    migrations = discover_migrations(path)
    if through_version < 1 or through_version > len(migrations):
        raise BaselineMismatchError(f"invalid baseline version: {through_version}")
    selected = migrations[:through_version]
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", LOCK_KEY)
        ledger_exists = await conn.fetchval("SELECT to_regclass('public.schema_migrations')")
        if ledger_exists is not None:
            count = await conn.fetchval("SELECT count(*) FROM schema_migrations")
            if count:
                raise BaselineMismatchError("cannot baseline a non-empty migration ledger")
        await assert_exact_baseline(conn, through_version)
        await conn.execute(LEDGER_SQL)
        for migration in selected:
            await conn.execute(
                "INSERT INTO schema_migrations (version, filename, checksum) VALUES ($1, $2, $3)",
                migration.version,
                migration.filename,
                migration.checksum,
            )
    return tuple(selected)
