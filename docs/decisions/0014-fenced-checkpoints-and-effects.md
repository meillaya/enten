---
title: "ADR 0014: Generation-fenced checkpoints and effects"
status: Accepted
date: 2026-07-15
deciders: enten maintainers
---

## Context

A lease can expire while a worker still runs. An HTTP timeout after a GitHub
POST cannot prove whether the write happened. Ordinary retries can write stale
state or duplicate comments and checks.

## Decision

Compare-and-swap fence every production review checkpoint by `claim_generation`
and an unexpired lease. Check and comment writes use separate durable effect
keys. Persist `prepared`, then `attempted`, call GitHub, then persist
`succeeded` with the external ID. An attempted effect without a durable receipt
becomes `indeterminate` and needs reconciliation.

## Consequences

- A stale generation cannot overwrite a replacement worker.
- Recovery favors duplicate prevention over availability.
- Checkpoint and effect rows become incident evidence.
- Operators need a deliberate procedure for ambiguous GitHub outcomes.
