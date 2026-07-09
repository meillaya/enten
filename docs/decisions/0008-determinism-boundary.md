---
title: ADR 0008 — Determinism boundary — LLM proposes, exit code arbitrates
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

LLMs are non-deterministic. If the agent asks an LLM whether a patch passed, the verdict can vary between runs even when the patch did not change. This makes evaluation and debugging unreliable.

## Decision

Draw a hard boundary:

- The **LLM is the proposer**. It generates reviews, patches, and tests. This step may be creative and context-heavy.
- The **sandbox exit code is the arbiter**. Whether a patch works is decided by running it in a fresh sandbox and reading the exit code and output.

The eval harness verifies the boundary by running the same fixture twice and asserting byte-identical `VerificationResult` JSON.

## Consequences

- Verdicts are reproducible for the same diff and base.
- The agent never uses an LLM as a judge of correctness.
- Some semantic failures that a human would catch but tests do not may be missed, but those are surfaced as new test cases rather than LLM guesses.
