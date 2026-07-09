---
title: ADR 0007 — Fast TDD tests and a separate eval harness
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

We need two kinds of confidence: fast feedback during development and rigorous measurement of agent quality. A single test suite cannot serve both needs well.

## Decision

Keep the two concerns separate:

- **Unit and integration tests** run with `pytest` and `vitest` on every commit. They mock LLMs and sandboxes by default and finish quickly.
- **Eval harness** is a standalone command, `enten eval`, that runs a fixture corpus with known expected findings and reports precision, recall, false-positive rate, and determinism.

## Consequences

- Developers get fast feedback from `make test`.
- Releases are gated on the slower but authoritative eval harness.
- The eval harness can use stubbed LLMs for determinism or real providers for realism.
- Maintaining two test surfaces requires discipline to keep fixtures and unit tests aligned.
