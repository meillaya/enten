from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import asyncpg

KNOWN_MIGRATION_HASHES: Final[dict[str, str]] = {
    "0001_init.sql": "bd93b1dc14fe04a30f55fb20e8a2728813821905ce566b97d251edd3d801433c",
    "0002_pgvector.sql": "66936808da3af9ac21fa41ed8575cf4d423c7c5e58e7203e4fd5e0bb65d6ae99",
}
MIGRATION_NAME: Final = re.compile(r"^(?P<version>[0-9]{4})_[a-z0-9_]+\.sql$")
LOCK_KEY: Final = 7_303_152_109_791_101_449
LEDGER_SQL: Final = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version integer PRIMARY KEY,
    filename text NOT NULL UNIQUE,
    checksum char(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT schema_migrations_version_check CHECK (version > 0),
    CONSTRAINT schema_migrations_checksum_check CHECK (checksum ~ '^[0-9a-f]{64}$')
)
"""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    filename: str
    checksum: str
    sql: str


class MigrationError(RuntimeError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def __str__(self) -> str:
        return self.detail


class MigrationDiscoveryError(MigrationError):
    pass


class ChecksumDriftError(MigrationError):
    pass


class BaselineMismatchError(MigrationError):
    pass


class UnsafeDowngradeError(MigrationError):
    pass


def discover_migrations(path: Path, *, verify_known: bool = True) -> tuple[Migration, ...]:
    if not path.is_dir():
        raise MigrationDiscoveryError(f"migration directory does not exist: {path}")
    migrations: list[Migration] = []
    for candidate in sorted(path.iterdir()):
        if candidate.suffix != ".sql":
            continue
        match = MIGRATION_NAME.fullmatch(candidate.name)
        if match is None or candidate.is_symlink() or not candidate.is_file():
            raise MigrationDiscoveryError(f"invalid migration file: {candidate.name}")
        raw = candidate.read_bytes()
        checksum = hashlib.sha256(raw).hexdigest()
        known = KNOWN_MIGRATION_HASHES.get(candidate.name)
        if verify_known and known is not None and checksum != known:
            raise ChecksumDriftError(
                f"known checksum drift for {candidate.name}: expected {known}, found {checksum}"
            )
        migrations.append(
            Migration(
                version=int(match.group("version")),
                filename=candidate.name,
                checksum=checksum,
                sql=raw.decode("utf-8"),
            )
        )
    versions = [migration.version for migration in migrations]
    if not versions or versions != list(range(1, len(versions) + 1)):
        raise MigrationDiscoveryError(
            f"migration versions must be contiguous from 0001: {versions}"
        )
    return tuple(migrations)


async def lock_and_create_ledger(conn: asyncpg.Connection) -> None:
    await conn.execute("SELECT pg_advisory_xact_lock($1)", LOCK_KEY)
    await conn.execute(LEDGER_SQL)


async def validate_ledger(conn: asyncpg.Connection, migrations: Sequence[Migration]) -> set[int]:
    rows = await conn.fetch(
        "SELECT version, filename, checksum FROM schema_migrations ORDER BY version"
    )
    by_version = {migration.version: migration for migration in migrations}
    applied: set[int] = set()
    for row in rows:
        migration = by_version.get(row["version"])
        if migration is None or row["filename"] != migration.filename:
            raise MigrationDiscoveryError(
                f"ledger version {row['version']} has unknown filename {row['filename']}"
            )
        recorded = str(row["checksum"]).strip()
        if recorded != migration.checksum:
            raise ChecksumDriftError(
                f"ledger checksum drift for {migration.filename}: "
                f"expected {migration.checksum}, found {recorded}"
            )
        applied.add(migration.version)
    if applied and applied != set(range(1, max(applied) + 1)):
        raise MigrationDiscoveryError(f"ledger versions are not a contiguous prefix: {applied}")
    return applied
