---
title: ADR 0001 — Single monorepo, not split repos
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

We need to ship a Python review worker and reusable analysis/model libraries alongside a TypeScript webhook edge. The question is whether to keep them in one repository or split them into separate repositories.

## Decision

A single repository with two language workspaces (`uv` for Python, `pnpm` for TypeScript) and a root `Makefile` orchestrating both.

## Consequences

- One PR can touch both languages atomically — important for contract changes such as the OpenTelemetry wire format shared by both halves.
- CI, tags, and release history stay in one repository; language manifest versions remain synchronized by review.
- We accept a slightly more complex root (`Makefile`, two lockfiles) in exchange for atomic cross-language changes.
- Alternatives considered and rejected:
  - **Two repos** with a `contracts/` published package — forces every cross-cutting change into a multi-repo PR, slowing iteration.
  - **Polyglot monorepo via Bazel/Pants** — enormous operational burden for a two-language project.
