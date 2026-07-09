---
title: ADR 0004 — pgvector instead of a dedicated vector database
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

The retrieval pipeline needs to store and search code embeddings. Dedicated vector databases (Pinecone, Weaviate, Chroma, Milvus) are designed for this, but they add cost, latency, and operational surface.

## Decision

Use the `pgvector` Postgres extension. Embeddings live in the same database as the job queue and review records, in a `vec.chunks` table with a `vector()` column and an IVFFlat index.

## Consequences

- No additional network hop or service to operate.
- Embeddings can be joined with job and review metadata in a single query.
- The `pgvector/pgvector:pg16` image ships the extension pre-installed, so local setup needs no custom Dockerfile.
- Very large corpora may eventually need a specialized vector store, but pgvector is sufficient for the target repository sizes.
