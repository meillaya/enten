---
title: ADR 0002 — Hybrid Python + TypeScript stack
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

The agent needs a reasoning loop, a tool registry, webhook intake, and an HTTP API. We could write all of this in one language. Should we?

## Decision

Split the work along the natural axis:

- **Python** owns the agent loop, planners, and tool implementations. Reasoning here benefits from pydantic, the rich `typer` ecosystem, and inference SDKs that are most mature in Python.
- **TypeScript** owns the network edge — webhook verification, HTTP routing, structured logging, and OpenTelemetry wiring. Hono is the lightest possible HTTP framework on Node 22, and `@octokit/webhooks` is canonical in TypeScript.

## Consequences

- Two deploy artifacts and two toolchains, but the runtime topology is the same: one container per half, one docker-compose for dev.
- A shared `contracts/` package defines the cross-language schema (event types, telemetry tags) so the two halves do not drift.
- We must keep test infrastructure honest across the language boundary.
- Alternatives considered and rejected:
  - **All Python** (FastAPI at the edge) — `@octokit/webhooks` would have to be re-implemented; Hono is materially faster and leaner than any Python framework for webhook fan-out.
  - **All TypeScript** (LangChain.js in the loop) — the inference SDKs and tool implementations we depend on are not yet stable enough in TypeScript to trust for a production agent.
