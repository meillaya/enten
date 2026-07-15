---
title: ADR 0013 — Docker-only verified release sandbox
status: Accepted
supersedes: ADR 0006
date: 2026-07-15
deciders: enten maintainers
---

## Context

The former E2B adapter used an unverified API contract and could create false operational confidence. A worker container using the host daemon also cannot bind a path that exists only inside that worker container.

## Decision

Docker is the sole verified backend. Selecting E2B raises before any network call. The Docker worker creates a read-only, networkless, non-root container with bounded tmpfs workspace and streams a validated archive into that tmpfs through the Docker exec socket. No worker-local host bind is used.

The trusted operator selects the verifier image and argv command; webhook payloads cannot change either.

## Consequences

- Self-hosted deployment needs a dedicated Docker host and socket access.
- No orphan checkout bind directories are required by new sandbox sessions.
- Hosted platforms without a supported Docker daemon cannot run the verified worker.
- E2B can return only through a separately approved SDK/dependency decision and equivalent lifecycle tests.
