---
title: "ADR 0002: Hybrid Python and TypeScript stack"
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

The system needs a durable review worker, reusable model and tool libraries,
webhook intake, and an HTTP API. One language could cover all four.

## Decision

Split the work along the natural axis:

- Python owns the worker, the review pipeline, the bounded local generator, and
  the optional model and tool libraries. The production worker does not start
  an LLM planner. Model-backed generation is an explicit library or CLI choice.
- TypeScript owns the network edge: webhook verification, HTTP routing,
  structured logging, and OpenTelemetry wiring. Hono supplies the small routing
  surface, and `@octokit/webhooks` supplies the signature implementation.

## Consequences

- Two process roles and two toolchains share one production image and one
  Compose topology.
- `contracts/` holds the strict job-envelope schema and shared fixtures,
  including trace carrier fields, so both parsers stay aligned.
- The test suite must cover the language boundary.
- All Python with FastAPI would require a second implementation of
  `@octokit/webhooks`. All TypeScript would move the Python indexing,
  verification, and model libraries without shrinking the edge trust boundary.
