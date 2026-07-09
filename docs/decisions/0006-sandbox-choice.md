---
title: ADR 0006 — Docker + E2B sandboxes; Daytona not supported
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

The agent needs an isolated environment to apply patches and run tests. Candidates include local Docker, E2B cloud sandboxes, and Daytona.

## Decision

Support two sandbox providers:

- **Docker** as the default for local development and self-hosted deployments.
- **E2B** as an optional cloud provider for environments without a local Docker daemon.

Daytona is intentionally not supported; it adds operational complexity without a compelling advantage over Docker + E2B for our use case.

## Consequences

- Local development works out of the box where Docker is available.
- Cloud scaling is available via E2B with an API key.
- The `SandboxProvider` protocol in `packages/python/enten/src/enten/sandbox/protocol.py` keeps the rest of the code independent of the backend.
- Docker requires a host daemon and careful hardening; E2B requires network access and an API key.
