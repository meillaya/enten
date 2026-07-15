---
title: ADR 0014 — Generation-fenced checkpoints and external effects
status: Accepted
date: 2026-07-15
deciders: enten maintainers
---

## Context

A lease can expire while a worker is still running, and an HTTP timeout after a GitHub POST cannot prove whether the write happened. Ordinary retries would allow stale state writes or duplicate comments/checks.

## Decision

Every production review checkpoint is compare-and-swap fenced by job `claim_generation` and an unexpired lease. GitHub check and comment writes have separate durable effect keys. Persist `prepared`, then `attempted`, perform the call, and persist `succeeded` with the external ID. An attempted effect without a durable receipt becomes terminal `indeterminate` and requires reconciliation.

## Consequences

- Replacement workers cannot be overwritten by stale generations.
- Automatic recovery prefers duplicate prevention over availability.
- Checkpoint and effect rows become part of incident evidence.
- Operators need a deliberate reconciliation procedure for ambiguous GitHub outcomes.
