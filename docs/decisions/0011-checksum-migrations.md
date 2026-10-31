---
title: "ADR 0011: Checksum-ledger SQL migrations"
status: Accepted
date: 2026-07-15
deciders: enten maintainers
---

## Context

The repository already used asyncpg and ordered SQL files. A second migration
framework would create competing schema histories. The release also needed
rerun safety, exclusion of concurrent migrators, drift detection, and guarded
adoption of an existing database.

## Decision

Keep SQL files and add a checksum ledger. Discover strict numeric filenames,
hash raw bytes, take a transaction-scoped advisory lock, verify every recorded
checksum, and apply all pending files in one transaction. Baseline an existing
database only after exact schema verification. Applied migrations are
immutable. Repair released schema forward.

## Consequences

- The project needs no new dependency family or metadata model.
- Repeated and concurrent application is safe.
- Whitespace changes to an applied migration show up as drift.
- Operators must preserve released migration bytes and back up production data
  before a repair.
