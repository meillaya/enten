# ADR 0001 — Single monorepo, not split repos

- **Status**: Accepted
- **Date**: 2026-07-09
- **Deciders**: enten maintainers
- **Supersedes**: —
- **Superseded by**: —

## Context

We need to ship an agent that has both a Python core (planners, tool
registry) and a TypeScript edge (webhooks, HTTP API, observability). The
question is whether to keep them in one repo or split them.

## Decision

A single repository with two language workspaces (`uv` for Python,
`pnpm` for TypeScript) and a root `Makefile` orchestrating both.

## Consequences

* One PR can touch both languages atomically — important for contract
  changes (e.g. the OpenTelemetry wire format shared by both halves).
* CI is easier: one source of truth for the version, one set of tags,
  one changelog.
* We accept a slightly more complex root (`Makefile`, two lockfiles) in
  exchange for atomic cross-language changes.

## Alternatives considered

* **Two repos** with a `contracts/` published package. Rejected: forces
  every cross-cutting change into a multi-repo PR, slowing iteration.
* **Polyglot monorepo via Bazel/Pants**. Rejected: enormous operational
  burden for a two-language project.
