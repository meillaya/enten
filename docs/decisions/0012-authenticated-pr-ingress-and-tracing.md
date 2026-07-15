---
title: ADR 0012 — Authenticated PR-only ingress and trusted trace propagation
status: Accepted
date: 2026-07-15
deciders: enten maintainers
---

## Context

The original manifest and documentation subscribed to event classes the worker could not consume. Public `traceparent` headers also cannot be trusted to choose internal trace identity.

## Decision

Subscribe only to `pull_request`. Enqueue `opened`, `reopened`, `synchronize`, and non-draft `ready_for_review`. Validate headers/content type and body size before parsing, verify the raw-body signature, then validate the strict versioned envelope.

Discard inbound public trace context. After authentication, create a producer span, store its W3C carrier and trace ID atomically with the job, and require the Python worker to validate and continue it as a consumer span.

## Consequences

- Smaller webhook and permission surface.
- Unsupported events are cheap acknowledged no-ops.
- Cross-language causality is preserved without accepting trace spoofing.
- New event classes require a new policy and contract version rather than permissive dictionaries.
