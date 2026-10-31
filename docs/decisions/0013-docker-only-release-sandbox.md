---
title: "ADR 0013: Docker-only verified release sandbox"
status: Accepted
supersedes: ADR 0006
date: 2026-07-15
deciders: enten maintainers
---

## Context

The E2B adapter used an unverified API contract and could create false
confidence. A worker container that uses the host daemon cannot bind a path that
exists only inside the worker container.

## Decision

Docker is the only verified backend. Selecting E2B raises before any network
call. The worker creates a read-only, networkless, non-root container with a
bounded tmpfs workspace. It streams a validated archive into that tmpfs through
the Docker exec socket. It does not bind a worker-local host path. The operator
selects the verifier image and argv command. Webhook payloads cannot change
either.

## Consequences

- Self-hosted deployment needs a dedicated Docker host and socket access.
- New sandbox sessions need no orphan checkout bind directories.
- A hosted platform without a Docker daemon cannot run the verified worker.
- E2B can return only through a new decision, an approved SDK dependency, and
  equivalent lifecycle tests.
