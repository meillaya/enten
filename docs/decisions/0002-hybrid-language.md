# ADR 0002 — Hybrid Python + TypeScript

- **Status**: Accepted
- **Date**: 2026-07-09
- **Deciders**: enten maintainers
- **Supersedes**: —
- **Superseded by**: —

## Context

The agent needs a reasoning loop, a tool registry, webhook intake, and an
HTTP API. We could write all of this in one language. Should we?

## Decision

Split the work along the natural axis:

* **Python** owns the agent loop, planners, and tool implementations.
  Reasoning here benefits from pydantic, the rich `typer` ecosystem, and
  inference SDKs that are most mature in Python.
* **TypeScript** owns the network edge — webhook verification, HTTP
  routing, structured logging, OpenTelemetry wiring. Hono is the
  lightest possible HTTP framework on Node 22, and `@octokit/webhooks`
  is canonical in TypeScript.

## Consequences

* Two deploy artifacts, two toolchains, but the runtime topology is the
  same: one container per half, one docker-compose for dev.
* A shared `contracts/` package (T05) defines the cross-language schema
  (event types, telemetry tags) so the two halves don't drift.
* We must keep test infrastructure (T06) honest across the boundary.

## Alternatives considered

* **All Python** (FastAPI at the edge). Rejected: `@octokit/webhooks`
  would have to be re-implemented; Hono is materially faster and leaner
  than any Python framework for webhook fan-out.
* **All TypeScript** (LangChain.js in the loop). Rejected: the inference
  SDKs and tool implementations we depend on are not yet stable enough
  in TypeScript to trust for a production agent.
