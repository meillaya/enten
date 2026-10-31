---
title: "ADR 0005: LLM cost tracking with a persisted-review cap"
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

LLM agents can consume tokens unpredictably. A bug or adversarial input can run
up cost before anyone notices.

## Decision

Log every completion with model, input and output tokens, USD cost, and
duration. When a matching `pr_reviews` row exists, associate the call through
`review_id`, accumulate it against that review, and check it against
`per_pr_cost_cap_usd`. Explicit library and eval callers without a persisted
review still write an unaffiliated audit row. They do not get a per-review cap.

## Consequences

- Operators can predict and audit spend for persisted reviews.
- The client stops persisted-review loops at the configured cap. Unaffiliated
  callers need their own outer budget.
- New models need a pricing entry in
  `packages/python/enten/src/enten/llm/models_pricing.py`.
- Token counting and database insertion add a small cost to each call.
