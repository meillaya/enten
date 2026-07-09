---
title: ADR 0003 — Postgres for the job queue
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

The worker needs a durable, observable job queue that supports idempotency, retries, and concurrent consumers. Redis is a common choice for job queues, but the project already requires Postgres for application data and pgvector for embeddings.

## Decision

Use Postgres as the job queue. Jobs are rows in a `jobs` table that the worker claims with `SELECT ... FOR UPDATE SKIP LOCKED`. State transitions, retry counts, and idempotency keys are stored in the same database as the rest of the application state.

## Consequences

- One less infrastructure dependency to operate in production and CI.
- ACID state transitions make retries and stale-job recovery straightforward.
- Queue depth and job state are visible with ordinary SQL.
- Throughput is bounded by Postgres row locking, but the expected workload (PR reviews) is well within that bound.
- Adding a dedicated queue service later remains possible if throughput requirements change.
