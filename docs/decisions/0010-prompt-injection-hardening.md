---
title: "ADR 0010: Prompt injection hardening"
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

Pull request diffs, titles, bodies, and comments are untrusted. An attacker can
embed instructions such as `ignore previous instructions` in a diff or comment.

## Decision

Harden the prompt pipeline with overlapping controls:

- Randomized XML-like boundaries wrap every untrusted block. Injected content
  cannot guess the closing tag.
- A kind annotation labels each block as `diff`, `pr_description`, `code`, or
  `comment`.
- System prompt rules forbid obeying instructions inside user content. Every
  finding must cite `file:line`.
- Output validation filters findings against available files and drops
  references that do not exist.
- System prompts are static templates. Only data blocks contain user content.

## Consequences

- Common injection patterns fail without a model change.
- Output validation rejects findings that cannot be verified.
- The controls overlap, so one failure does not remove the others.
- Adversarial prompts remain a risk. The fixture corpus includes a
  `prompt_injection` category for regression testing.
