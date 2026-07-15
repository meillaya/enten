# enten — deterministic pull-request review infrastructure

> Authenticate GitHub pull requests, run a trusted verifier in a hardened sandbox, and report an exit-code-derived verdict without repository write access.

[![CI](https://github.com/Meillaya/enten/actions/workflows/ci.yml/badge.svg)](https://github.com/Meillaya/enten/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/Meillaya/enten)](LICENSE)

`enten` is a self-hosted GitHub App and local CLI for evidence-grounded pull-request review. The TypeScript edge authenticates a narrow set of GitHub events, Postgres provides the durable queue and crash-fenced checkpoints, and the Python worker verifies an exact public PR head in Docker. GitHub Checks and PR comments are external effects with durable attempt receipts, so an ambiguous write is never blindly repeated.

The release pipeline deliberately keeps the verdict deterministic: findings may be heuristic or model-assisted, but only the configured verifier process exit code decides pass or fail. The shipped worker uses the bounded local findings generator; OpenAI-compatible and Ollama provider libraries plus the independent eval harness are included, but production LLM review is not enabled implicitly.

## Quickstart

Run the local pipeline without GitHub credentials:

```bash
git clone https://github.com/Meillaya/enten.git
cd enten
make install
uv run enten review --repo . --verify-command "python -m compileall -q ."
make eval
```

Run the complete release gate:

```bash
make check
```

The local command writes replayable state to `.enten/reviews.json`, never posts to GitHub, and accepts an argv-only verification command (no shell expansion).

## Architecture

```text
GitHub pull_request
        │ signed request
        ▼
TypeScript/Hono edge ── authenticated W3C carrier ──► Postgres job queue
                                                        │ fenced claim
                                                        ▼
                                                Python worker
                                             ┌──────────┼──────────┐
                                             │          │          │
                                      public SHA   Docker verifier  durable report
                                      checkout     (exit arbiter)   receipts
                                             │          │          │
                                             └──────────┴──────► GitHub Check/comment
```

Key boundaries:

- **Ingress:** only `pull_request` actions `opened`, `reopened`, `synchronize`, and non-draft `ready_for_review`; unsupported events are acknowledged and ignored.
- **Repository:** public GitHub repositories only. The worker fetches the exact `refs/pull/<n>/head`, disables credential helpers, validates the canonical origin and head SHA, and removes the temporary checkout.
- **Persistence:** checksum-verified SQL migrations, verifier-contract-bound job identity, generation-CAS queue leases renewed across checkout, verification, and reporting, compare-and-swap stage checkpoints, and durable external-effect states (`prepared`, `attempted`, `succeeded`, `indeterminate`).
- **Verification:** Docker is the only verified backend. Containers use a read-only root filesystem, tmpfs workspace, no network, no capabilities, a non-root user, and bounded CPU, memory, PID, and time limits.
- **Authority:** the GitHub App can read pull requests and write PR comments/check runs. It cannot write repository contents, Actions, or issues.
- **Tracing:** the public `traceparent` is discarded. The authenticated edge creates the trusted producer context; the worker verifies and continues that carrier.

See [the architecture guide](docs/architecture.md), [permission contract](docs/permissions.md), and [ADRs](docs/decisions/).

## Why this exists

- **A verifier should arbitrate.** Model output can propose a finding, test, or patch, but it cannot declare its own work correct.
- **Crash safety matters at API boundaries.** A timeout after a GitHub POST is ambiguous. Retrying it can duplicate a check or comment, so `enten` records attempts before the network call and stops on ambiguity.
- **Untrusted PRs need a small boundary.** The release refuses private checkouts, arbitrary webhook commands, host-shell expansion, unsafe paths, symlinks, submodules, conventional secret-shaped paths, and networked verifier containers. App and model credentials remain in operator services and are not injected into the verifier container.
- **Quality claims need independent inputs.** Stub findings are separate from expected eval labels; missing predictions, zero collected tests, malformed reports, and unavailable scanners fail closed.

## How it works

1. The Hono edge validates the signature header and content type before reading a size-limited raw body, verifies the SHA-256 HMAC, then validates event and delivery headers, binds the immutable verifier image and command into the strict versioned job identity, and atomically enqueues it.
2. The worker claims a job with a generation and lease, renews that exact generation throughout the claimed-job scope, rejects a mismatched verifier contract, validates the authenticated trace carrier, and fetches the exact public PR ref into a mode-0700 temporary checkout. A stale generation cannot renew, checkpoint, report, or complete the job.
3. The shared stage machine discovers bounded source evidence, generates anchored findings, runs the trusted verification command in the selected immutable Docker image, and persists each stage.
4. The exit-code arbiter emits `pass`, `flaky`, `fail`, or `error`. Findings cannot override that verdict.
5. A fenced reporter records the check attempt before calling GitHub, persists the receipt, then does the same for the PR comment. Any uncertain outcome becomes terminal `indeterminate` for operator reconciliation.

## Try it locally

### Infrastructure only

```bash
scripts/dev-up.sh --infra-only
./scripts/db-migrate.sh
scripts/dev-down.sh
```

This starts loopback-bound Postgres/pgvector and Jaeger without requiring GitHub credentials.

### Full GitHub App stack

1. Replace the example webhook origin in `packages/ts/app/github-app.yml` with the externally reachable HTTPS origin that routes to this edge, then create the GitHub App and install it on a **public test repository**. The manifest contains a valid URL-shaped placeholder, not environment-variable interpolation.
2. Copy `.env.example` to `.env`; replace `GITHUB_APP_ID`, `GITHUB_WEBHOOK_SECRET`, and `GITHUB_APP_PRIVATE_KEY_PATH` with real values.
3. For a credentialed smoke test, keep the generic immutable Python image and syntax-only command from `.env.example`:

```dotenv
SANDBOX_DOCKER_IMAGE=python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28
VERIFICATION_COMMAND=python -m compileall -q .
```

This verifies that the authenticated fetch, sandbox, and reporting path works for a Python repository. It is not a substitute for that repository's test suite.

4. Start the stack:

```bash
scripts/dev-up.sh
scripts/healthcheck-all.sh
curl --fail http://127.0.0.1:3000/health
open http://127.0.0.1:16686
```

`healthcheck-all.sh` probes the running Compose services, Postgres, and the edge. It does not run the static release suite; use `make check` separately for lint, typecheck, build, coverage, eval, permission, secret, and dependency gates.

### Offline HTTPie verifier fixture

The pinned HTTPie image is an offline release fixture. It accepts only the recorded `httpie/cli` commit, normalized source digest, and pytest selector, so it intentionally rejects fork commits and must not be configured for a credentialed GitHub smoke test.

Run the opt-in integration test against an exact trusted checkout; the test builds its own uniquely tagged image:

```bash
ENTEN_RUN_DEMO_IMAGE_INTEGRATION=1 \
  ENTEN_HTTPIE_SOURCE=/path/to/exact/httpie-checkout \
  uv run --project packages/python/enten pytest tests/test_demo_image.py -q
```

The test verifies the image's immutable source contract and removes its temporary image and containers. See [the demo contract](docs/demo-repo.md) for the pinned commit and tree. Production verifier references must be either `sha256:<64-hex>` image IDs or registry `name@sha256:<64-hex>` references; the worker rejects mutable tags before creating a Docker client.

The self-hosted worker needs access to `/var/run/docker.sock`; `scripts/dev-up.sh` supplies the socket group ID and rejects missing credentials or a non-file private-key path. Treat Docker socket access as host-level authority and dedicate the worker host accordingly.

## Supported and deferred surfaces

| Surface | Release status |
|---|---|
| Local CLI review/cost/eval | Verified |
| Signed GitHub PR ingress and Postgres queue | Verified |
| Public exact-SHA checkout | Verified with local/stub Git remotes; live GitHub requires App setup |
| Docker verifier | Verified |
| GitHub check/comment reporter | Contract-tested; live posting requires GitHub credentials |
| OpenAI-compatible and Ollama clients | Unit/eval tested; not wired into the default worker generator |
| E2B | Fails closed; no supported SDK integration in this release |
| Fly.io/Railway files | Topology templates only; hosted Docker-socket verification is not validated |
| Private repositories, dashboard, multi-tenant auth | Out of scope |

## Development

The hermetic developer shell is declared by `devenv.nix` (Python 3.12, Node 22, pnpm, uv, PostgreSQL/pgvector, scanners, and Docker tooling).

```bash
devenv test
make help
make test
make coverage
make security
```

No new dependency family is required for the release gates. Ruff is invoked as a pinned standalone tool; TypeScript uses real `tsc`, build, Vitest, and coverage gates rather than placeholder lint scripts.

## Operations and security

- [Deployment templates and limitations](docs/deploy.md)
- [Runbook](docs/runbook.md)
- [Maintainer response policy](docs/oncall.md)
- [Demo contract](docs/demo-repo.md)
- [GitHub permissions](docs/permissions.md)

Do not run global Docker prune commands on shared hosts. Cleanup by Compose project, sandbox label, exact container ID, or test-session label only.

## License

`enten` is released under the [MIT License](LICENSE).
