---
title: "ADR 0009: Minimal GitHub App permission scope"
status: Superseded in part by ADR 0012
date: 2026-07-09
deciders: enten maintainers
---

## Status

The permission set stands. ADR 0012 supersedes the event set.

## Context

The bot reads pull requests and posts results. It must not modify repository
contents or run workflows. An over-scoped App widens the blast radius if
credentials leak.

## Decision

Grant three permissions:

- `pull_requests: write` posts review comments.
- `checks: write` creates Check Runs with verification results.
- `metadata: read` reads repository metadata required by the GitHub App API.

Subscribe only to `pull_request`; ADR 0012 defines the accepted actions. Deny
`contents: write`, `actions: write`, and every write scope beyond checks and
pull-request comments.

## Consequences

- The bot cannot push commits, merge pull requests, or trigger workflows.
- A leaked credential can comment and create checks. It cannot change code.
- The manifest in `packages/ts/app/github-app.yml` and the audit script in
  `scripts/check-permissions.sh` define and check the scope.
