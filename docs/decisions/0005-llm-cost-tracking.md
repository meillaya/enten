---
title: ADR 0005 — Mandatory LLM cost tracking with a per-run cap
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

LLM agents can consume tokens unpredictably. Without tracking, a bug or adversarial input can run up significant cost before anyone notices.

## Decision

Every LLM completion must be logged with model, input/output tokens, USD cost, duration, and the associated `run_id`. The `pr_reviews` table accumulates spend per run and enforces a configurable `per_pr_cost_cap_usd`. The worker aborts the run before the cap is exceeded.

## Consequences

- Operators can predict and audit spend per PR.
- Runaway loops are stopped automatically.
- New models require a pricing entry in `packages/python/enten/src/enten/llm/models_pricing.py`.
- There is a small per-call overhead for token counting and database insertion.
