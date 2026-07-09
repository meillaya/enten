# Architecture

`enten` is a queue-driven agent. The TypeScript edge receives untrusted GitHub events, validates them, and writes jobs to Postgres. The Python worker claims jobs, runs an LLM-powered review/verify pipeline inside a sandbox, and reports back to GitHub. Observability and cost tracking are first-class.

## Component diagram

```
                 webhook + signature
┌─────────┐            │            ┌─────────────────────┐
│ GitHub  │────────────┼───────────>│  TS edge (Hono)     │ packages/ts/app/src/server.ts
│         │            │            │  routes/webhook.ts  │
└─────────┘            │            └──────────┬──────────┘
                       │                       │ enqueue
                       │                       ▼
                       │      ┌──────────────────────────────────────┐
                       │      │  Postgres + pgvector                 │ docker-compose.yml
                       │      │  jobs | pr_reviews | llm_calls       │ packages/python/enten/src/enten/queue/models.py
                       │      │  sandbox_runs | vec.chunks           │
                       │      └──────────────────┬───────────────────┘
                       │                         │
                       │ claim_next              │
                       └─────────────────────────┘
                                                 │
                                                 ▼
                                    ┌──────────────────────┐
                                    │   Python worker      │ packages/python/enten/src/enten/worker.py
                                    │   state machine      │
                                    └──────────┬───────────┘
                                               │
              ┌────────────────────────────────┼────────────────────────────────┐
              │                                │                                │
              ▼                                ▼                                ▼
    ┌────────────────────┐          ┌────────────────────┐          ┌────────────────────┐
    │  LLM client        │          │  Sandbox provider  │          │  GitHub client     │
    │  (proposer)        │          │  (arbiter)         │          │  (reporter)        │
    │  packages/python/  │          │  packages/python/  │          │  packages/python/  │
    │  enten/src/enten/  │          │  enten/src/enten/  │          │  enten/src/enten/  │
    │  llm/client.py     │          │  sandbox/docker.py │          │  github/client.py  │
    │  llm/types.py      │          │  sandbox/e2b.py    │          │  reporting/        │
    │  review/generator. │          │  sandbox/protocol. │          │  comment.py        │
    │  py                │          │  py                │          │  checks.py         │
    └────────────────────┘          └────────────────────┘          └────────────────────┘
```

## Components

- **GitHub** — source of `pull_request`, `pull_request_review`, and `issue_comment` events. The bot is installed on a fork and receives signed webhooks.
- **TS edge** — `packages/ts/app/src/server.ts` boots a Hono app. `routes/webhook.ts` verifies the GitHub signature, computes an idempotency key, and calls `queue/jobs.ts` to persist a job. `routes/health.ts` and `routes/metrics.ts` expose liveness and Prometheus metrics.
- **Postgres** — single source of truth for the job queue, PR reviews, LLM call logs, sandbox run records, and pgvector embeddings. Schema is in `migrations/` and models in `packages/python/enten/src/enten/queue/models.py`.
- **Python worker** — `packages/python/enten/src/enten/worker.py` polls with `claim_next`, recovers stale jobs, and executes the pipeline. It transitions jobs through the state machine and reports back.
- **LLM client** — `packages/python/enten/src/enten/llm/client.py` wraps providers and records every call's cost in `llm_calls`. `review/generator.py` uses it to propose findings and patches.
- **Sandbox provider** — `packages/python/enten/src/enten/sandbox/protocol.py` defines `SandboxProvider`. `docker.py` runs hardened local containers; `e2b.py` runs cloud sandboxes. The exit code from `run()` is the deterministic arbiter.
- **Verification engine** — `packages/python/enten/src/enten/verification/verify.py` applies patches and runs tests. `verification/flaky.py` reruns tests to detect flakiness.
- **GitHub reporter** — `packages/python/enten/src/enten/reporting/comment.py` renders markdown PR comments; `reporting/checks.py` posts Check Runs.
- **Indexer/retrieval** — `packages/python/enten/src/enten/indexer/pipeline.py` chunks code with tree-sitter and stores embeddings; `retrieval/` performs hybrid BM25 + pgvector search.
- **Eval harness** — `packages/python/enten/src/enten/eval/` runs fixture-based evaluation with stubbed LLMs and checks precision/recall/determinism.
- **Observability** — `packages/python/enten/src/enten/otel.py` and `packages/ts/app/src/otel.ts` emit OTLP traces to Jaeger. `packages/python/enten/src/enten/metrics.py` and `packages/ts/app/src/metrics.ts` expose Prometheus metrics.

## Job state machine

```
        ┌─────────┐
        │ queued  │
        └────┬────┘
             │ claim_next / retry
             ▼
       ┌───────────┐
       │ indexing  │
       └─────┬─────┘
             │
             ▼
      ┌────────────┐
      │  reviewing │
      └─────┬──────┘
            │
            ▼
      ┌────────────┐
      │   testing  │
      └─────┬──────┘
            │
            ▼
      ┌────────────┐
      │  verifying │
      └─────┬──────┘
            │
            ▼
      ┌────────────┐
      │ commenting │
      └─────┬──────┘
            │
            ▼
        ┌───────┐
        │  done │
        └───────┘
```

Any state can transition to `failed` when the retry budget is exhausted, or back to `queued` while retries remain. Webhook ingestion only creates `queued` rows.

## Determinism boundary

The boundary separates *proposal* from *judgement*:

- **Proposer (LLM).** The LLM generates a review, a patch, or a test. This step is allowed to be creative, context-heavy, and slightly non-deterministic.
- **Arbiter (sandbox exit code).** Whether the patch works is decided by running it in a fresh sandbox and reading the exit code. The same diff applied to the same base must produce the same exit code, stdout, and stderr.

This design means the agent never asks the LLM "did this pass?". The LLM only produces candidates; the sandbox produces verdicts. The eval harness locks this boundary by running the same fixture twice and asserting byte-identical `VerificationResult` JSON.

## Cost tracking dashboard

Every LLM completion is recorded in the `llm_calls` table with model, input/output tokens, USD cost, duration, and the associated `run_id`. The `pr_reviews` table accumulates spend per run and enforces `per_pr_cost_cap_usd`. A simple dashboard can be built by querying:

```sql
SELECT run_id, SUM(cost_usd) AS spend, SUM(tokens_in + tokens_out) AS tokens
FROM llm_calls
GROUP BY run_id
ORDER BY spend DESC;
```

Alerts fire when a run approaches its cap; the worker aborts before the cap is exceeded.

## See also

- [`docs/decisions/`](decisions/) — ADRs covering the monorepo, language split, Postgres queue, pgvector, cost tracking, sandboxes, TDD/eval split, determinism boundary, GitHub permissions, and prompt-injection hardening.
- [`docs/demo-repo.md`](demo-repo.md) — end-to-end validation scenario.
