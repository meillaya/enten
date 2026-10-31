---
title: "ADR 0008: LLM proposes, exit code arbitrates"
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

LLMs are non-deterministic. If an LLM judges whether a patch passed, the
verdict can change between runs even when the patch did not.

## Decision

Keep the boundary hard:

- The LLM proposes. It generates reviews, patches, and tests. This step can be
  creative and context-heavy.
- The sandbox exit code arbitrates. A fresh sandbox runs the patch, and the exit
  code and output decide whether it works.

The eval harness runs the same fixture twice and asserts byte-identical
`VerificationResult` JSON.

## Consequences

- Verdicts repeat for the same diff and base.
- The agent never uses an LLM as a judge of correctness.
- A semantic failure that tests miss can pass. Add a test case for it instead
  of asking the model.
