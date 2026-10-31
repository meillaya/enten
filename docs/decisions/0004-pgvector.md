---
title: "ADR 0004: pgvector instead of a dedicated vector database"
status: Accepted
date: 2026-07-09
deciders: enten maintainers
---

## Status

Accepted.

## Context

The retrieval pipeline stores and searches code embeddings. Dedicated vector
databases such as Pinecone, Weaviate, Chroma, and Milvus add cost, latency, and
operational surface.

## Decision

Use the `pgvector` extension. Embeddings live beside the job queue and review
records in a `vec.chunks` table with a `vector(1024)` column. Approximate cosine
search uses an HNSW index with `m = 16` and `ef_construction = 64`.

## Consequences

- Embeddings and review metadata are joined in one query.
- The pinned `pgvector/pgvector:pg16` image ships the extension, so local setup
  uses the stock image.
- Very large corpora may need a specialized vector store. The target repository
  sizes fit pgvector.
