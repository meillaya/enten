---
title: "ADR 0001: Single monorepo, not split repos"
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

The project ships a Python review worker and analysis libraries next to a
TypeScript webhook edge. The choice is one repository or separate repositories.

## Decision

Keep one repository with two language workspaces (`uv` for Python, `pnpm` for
TypeScript) and a root `Makefile` that runs both.

## Consequences

- One pull request can change both languages at once. That matters for shared
  contracts such as the OpenTelemetry wire format.
- CI, tags, and release history stay in one place. Review keeps the language
  manifest versions in sync.
- The root carries a `Makefile` and two lockfiles. That cost buys atomic
  cross-language changes.
- Two repositories with a published `contracts/` package would force every
  shared change into a multi-repo pull request. A Bazel or Pants monorepo would
  add an operational layer the two-language project does not need.
