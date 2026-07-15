from enten.migration_baseline import baseline_existing
from enten.migration_cli import main
from enten.migration_core import (
    KNOWN_MIGRATION_HASHES,
    BaselineMismatchError,
    ChecksumDriftError,
    Migration,
    MigrationDiscoveryError,
    MigrationError,
    UnsafeDowngradeError,
    discover_migrations,
)
from enten.migration_operations import (
    apply_migrations,
    assert_local_downgrade,
    downgrade_migrations,
)

__all__ = [
    "KNOWN_MIGRATION_HASHES",
    "BaselineMismatchError",
    "ChecksumDriftError",
    "Migration",
    "MigrationDiscoveryError",
    "MigrationError",
    "UnsafeDowngradeError",
    "apply_migrations",
    "assert_local_downgrade",
    "baseline_existing",
    "discover_migrations",
    "downgrade_migrations",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
