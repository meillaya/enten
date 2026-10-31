---
title: "ADR 0007: Fast tests and a separate eval harness"
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

The project needs fast feedback during development and a measured view of agent
quality. One test suite serves neither need well.

## Decision

Keep the two concerns separate:

- Unit and integration tests run with `pytest` and `vitest` on every commit.
  They mock LLMs and sandboxes and finish quickly.
- The eval harness is the standalone `enten eval` command. It runs a fixture
  corpus with known expected findings and reports precision, recall,
  false-positive rate, and determinism.

## Consequences

- `make test` gives fast feedback to developers.
- Releases also run the slower eval harness.
- The harness can use a stub LLM for determinism or a real provider for
  realism.
- Two test surfaces need disciplined fixture and unit-test alignment.
