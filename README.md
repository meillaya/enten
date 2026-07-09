# enten — GitHub-integrated AI engineering agent with deterministic verification

> Ship safer patches by proving every change in a sandbox before a human reviews it.

[![CI](https://github.com/<owner>/enten/actions/workflows/ci.yml/badge.svg)](https://github.com/<owner>/enten/actions/workflows/ci.yml)
[![Eval Smoke](https://github.com/<owner>/enten/actions/workflows/ci.yml/badge.svg?job=python-eval-smoke)](https://github.com/<owner>/enten/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/<owner>/enten)](LICENSE)

`enten` is an AI engineering agent that lives on your Git repositories. It ingests pull requests, plans changes, runs tools, executes tests in an isolated sandbox, and ships verified patches back as PR comments. This monorepo contains the Python reasoning core, the TypeScript webhook/API edge, the Postgres-backed job queue, and a deterministic evaluation harness.

## Quickstart

```bash
git clone https://github.com/<owner>/enten.git
cd enten
make install          # uv sync + pnpm install
docker compose up -d  # Postgres + Jaeger
make dev              # TS edge + Python worker
gh repo fork httpie/cli --clone=false --remote=true
# Open a PR against your fork and watch enten review it.
```

Run `make help` to see every target.

## What's in this repo

```
enten/
├── packages/python/enten/   # Python agent core
├── packages/ts/app/         # TypeScript webhook/API edge
├── docs/                    # Architecture + ADRs
├── scripts/                 # Dev/migration helpers
├── tests/                   # Cross-language tests
├── docker-compose.yml       # Local data stores
└── Makefile                 # Cross-language orchestration
```

## Architecture

For the high-level design, component diagram, job state machine, and determinism boundary, see [`docs/architecture.md`](docs/architecture.md). The ADRs in [`docs/decisions/`](docs/decisions/) record why we chose the monorepo layout, the hybrid language stack, Postgres as the queue, pgvector for retrieval, Docker + E2B sandboxes, and more.

## Why this exists

- **Determinism boundary.** LLMs are great proposers and terrible arbiters. `enten` separates the creative step (plan/patch generation) from the verdict step (sandbox execution with exit-code and output comparison). The same input must produce the same result every time.
- **Eval harness first.** Every change is validated against a fixture corpus with known expected findings. Precision, recall, false-positive rate, and determinism are measured before a feature ships.
- **Cost tracking by default.** Every LLM call is logged with model, tokens, and price. A configurable cap prevents runaway spend.

## How it works

1. **Ingest.** GitHub delivers `pull_request` and `issue_comment` events to the Hono webhook receiver (`packages/ts/app/src/routes/webhook.ts`). Signature verification and idempotency checks happen here, and the event is enqueued as a row in Postgres.
2. **Dispatch.** The Python worker (`packages/python/enten/src/enten/worker.py`) claims the next queued job with `SELECT ... FOR UPDATE SKIP LOCKED`, then moves it through a deterministic state machine.
3. **Plan & patch.** The review generator (`packages/python/enten/src/enten/review/generator.py`) reads the PR diff, repository context, and tool outputs, then proposes a patch using a provider-agnostic LLM client (`packages/python/enten/src/enten/llm/client.py`).
4. **Verify.** The verification engine (`packages/python/enten/src/enten/verification/verify.py`) applies the patch inside a sandbox and runs the project's test command. The sandbox provider (`packages/python/enten/src/enten/sandbox/docker.py` or `e2b.py`) returns exit code, stdout, and stderr; the exit code is the arbiter.
5. **Report.** The reporter (`packages/python/enten/src/enten/reporting/comment.py`) posts a structured PR comment with findings, verification status, and cost. Metrics and traces flow to Prometheus and Jaeger.

## Try it locally

```bash
# 1. Start the data stores
docker compose up -d postgres jaeger

# 2. Apply the database schema
./scripts/db-migrate.sh

# 3. Start the worker and webhook edge
make dev

# 4. In another terminal, run the eval smoke against stubbed LLMs
uv run enten eval --corpus tests/fixtures/prs/smoke --stub-llm

# 5. Inspect traces
open http://localhost:16686
```

## How to extend

- **Swap the LLM provider.** Implement the protocol in `packages/python/enten/src/enten/llm/client.py` and set `LLM_PROVIDER` in `.env`. The rest of the agent stays unchanged.
- **Swap the sandbox.** Add a new provider under `packages/python/enten/src/enten/sandbox/` that implements `SandboxProvider` (`packages/python/enten/src/enten/sandbox/protocol.py`). Existing verification code uses the protocol, not the implementation.

## Development prerequisites

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Node.js 22 and [pnpm 11.10.0](https://pnpm.io/)
- Docker Compose
- A GitHub App with webhook secret (see `github-app.yml`)

## License

`enten` is released under the [MIT License](LICENSE).
