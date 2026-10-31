---
title: "ADR 0003: Postgres for the job queue"
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

The worker needs a durable, observable job queue with idempotency, retries, and
concurrent consumers. Redis is a common choice for job queues. The project
already needs Postgres for application data and pgvector for embeddings.

## Decision

Use Postgres as the job queue. Jobs are rows in a `jobs` table. The worker
claims one with `SELECT ... FOR UPDATE SKIP LOCKED`. State transitions, retry
counts, and idempotency keys live in the same database as the rest of the
application state.

## Consequences

- The deployment runs one fewer service.
- ACID state transitions make retries and stale-job recovery straightforward.
- Queue depth and job state are visible with ordinary SQL.
- Postgres row locking bounds throughput. The expected review load fits that
  bound.
- A dedicated queue service can replace Postgres later if throughput changes.
