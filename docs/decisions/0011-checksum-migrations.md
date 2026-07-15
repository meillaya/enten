---
title: ADR 0011 — Checksum-ledger SQL migrations
status: Accepted
date: 2026-07-15
deciders: enten maintainers
---

## Context

The repository already used asyncpg and ordered SQL files. Adding a second migration framework would create competing schema histories, while the release also needed rerun safety, concurrent migrator exclusion, drift detection, and guarded adoption of an existing database.

## Decision

Keep SQL migrations and add a checksum ledger. Discover strict numeric filenames, hash raw bytes, acquire a transaction-scoped advisory lock, verify every recorded checksum, and apply all pending files transactionally. Existing databases may be baselined only after exact schema verification.

Applied migrations are immutable. Rollback of released schema is a forward repair, not destructive down SQL.

## Consequences

- No new dependency family or separate metadata model.
- Repeated and concurrent application is safe.
- Whitespace changes to an applied migration are detected as drift.
- Operators must preserve released migration bytes and back up production data before repair.
