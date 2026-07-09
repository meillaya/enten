---
title: ADR 0010 — Prompt injection hardening
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

Pull request diffs, titles, bodies, and comments are untrusted. An attacker could embed instructions such as "ignore previous instructions" inside a diff or comment to manipulate the agent.

## Decision

Harden the prompt pipeline with multiple layers:

- **Randomized XML-like boundaries** wrap every untrusted block, so injected content cannot craft a matching closing tag.
- **Kind annotation** labels each block as `diff`, `pr_description`, `code`, or `comment` so the model knows the source.
- **System prompt rules** explicitly forbid obeying instructions inside user-supplied content and require every finding to cite `file:line`.
- **Output validation** filters findings against available files and suppresses hallucinated references.
- **No user content in system instructions** — system prompts are static templates; only data blocks contain user content.

## Consequences

- Common injection patterns are neutralized without changing the model.
- Output validation catches attempts to produce unverifiable findings.
- The defense is layered; a failure in one control is backed up by the others.
- Adversarial prompts remain an ongoing concern; the fixture corpus includes a `prompt_injection` category for regression testing.
