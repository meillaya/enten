---
title: "ADR 0012: Authenticated PR ingress and trusted tracing"
status: Accepted
date: 2026-07-15
deciders: enten maintainers
---

## Context

The first manifest subscribed to event classes the worker could not consume.
Public `traceparent` headers cannot choose internal trace identity.

## Decision

Subscribe only to `pull_request`. Enqueue `opened`, `reopened`, `synchronize`,
and non-draft `ready_for_review`. Validate headers, content type, and body size
before parsing. Verify the raw-body signature, then validate the versioned
envelope.

Discard inbound public trace context. After authentication, create a producer
span, store its W3C carrier and trace ID with the job in one transaction, and
require the Python worker to validate and continue it as a consumer span.

## Consequences

- The webhook and permission surface is smaller.
- Unsupported events return a cheap acknowledgement.
- Causality survives the language boundary without trace spoofing.
- A new event class needs a policy and contract version, not a permissive
  dictionary.
