---
title: ADR 0009 — Minimal GitHub App permission scope for the bot
status: Superseded in part by ADR 0012
date: 2026-07-09
deciders: enten maintainers
---

## Status

Permission set remains accepted. The event set is superseded by ADR 0012.

## Context

The bot needs to read pull requests and post results, but it should not be able to modify repository contents or execute workflows. Over-scoping a GitHub App increases blast radius if credentials leak.

## Decision

Grant the smallest set of permissions that supports the feature:

- `pull_requests: write` — post review comments.
- `checks: write` — create Check Runs with verification results.
- `metadata: read` — read repository metadata required by the GitHub App API.

Subscribe only to `pull_request`; ADR 0012 defines the accepted action set.

Explicitly denied: `contents: write`, `actions: write`, and any other write scope beyond checks and pull-request comments.

## Consequences

- The bot cannot push commits, merge PRs, or trigger workflows.
- A leaked credential can comment and create checks, but cannot modify code.
- The manifest in `packages/ts/app/github-app.yml` and the audit script in `scripts/check-permissions.sh` are the single sources of scope truth.
