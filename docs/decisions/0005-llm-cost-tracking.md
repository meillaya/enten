---
title: ADR 0005 — LLM cost tracking with a persisted-review cap
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

LLM agents can consume tokens unpredictably. Without tracking, a bug or adversarial input can run up significant cost before anyone notices.

## Decision

Every LLM completion is logged with model, input/output tokens, USD cost, and duration. When the caller has first created a matching `pr_reviews` row, the call is associated through `review_id`, accumulated against that review, and checked against `per_pr_cost_cap_usd`. Explicit library and eval callers without a persisted review still produce an unaffiliated audit row, but do not receive a per-review cap.

## Consequences

- Operators can predict and audit spend for persisted PR reviews.
- The client stops persisted-review loops at the configured cap; unaffiliated explicit callers require their own outer budget.
- New models require a pricing entry in `packages/python/enten/src/enten/llm/models_pricing.py`.
- There is a small per-call overhead for token counting and database insertion.
