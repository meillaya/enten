---
title: ADR 0002 — Hybrid Python + TypeScript stack
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

The system needs a durable review worker, reusable model/tool libraries, webhook intake, and an HTTP API. We could write all of this in one language. Should we?

## Decision

Split the work along the natural axis:

- **Python** owns the worker, deterministic review pipeline, bounded local generator, and optional model/tool libraries. The production worker does not silently activate an LLM planner; model-backed generation is an explicit library or CLI choice.
- **TypeScript** owns the network edge — webhook verification, HTTP routing, structured logging, and OpenTelemetry wiring. Hono is the lightest possible HTTP framework on Node 22, and `@octokit/webhooks` is canonical in TypeScript.

## Consequences

- Two process roles and two toolchains share one production image and one Compose topology.
- A shared `contracts/` package defines the cross-language schema (event types, telemetry tags) so the two halves do not drift.
- We must keep test infrastructure honest across the language boundary.
- Alternatives considered and rejected:
  - **All Python** (FastAPI at the edge) — `@octokit/webhooks` would have to be re-implemented; Hono is materially faster and leaner than any Python framework for webhook fan-out.
  - **All TypeScript** — it would move the existing Python indexing, verification, and optional model libraries without reducing the network-edge trust boundary.
