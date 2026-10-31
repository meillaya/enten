---
title: "ADR 0006: Docker and E2B sandboxes, Daytona not supported"
status: Superseded by ADR 0013
date: 2026-07-09
deciders: enten maintainers
---

## Status

Superseded by ADR 0013. The E2B adapter did not meet the verified release
contract and now fails closed.

## Context

The agent needs an isolated environment to apply patches and run tests. The
candidates were local Docker, E2B cloud sandboxes, and Daytona.

## Decision

The original decision supported Docker as the default and E2B as an optional
cloud provider. Daytona was rejected because it added operational complexity
without an advantage over Docker and E2B. ADR 0013 now makes Docker the only
verified backend.

## Consequences

- Local development works where Docker is available.
- The `SandboxProvider` protocol in
  `packages/python/enten/src/enten/sandbox/protocol.py` keeps the rest of the
  code independent of the backend.
- Docker needs a host daemon and careful hardening. E2B needs network access and
  an API key.
- E2B stays closed until a new decision and equivalent lifecycle tests land.
