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
- **Persistence:** checksum-verified SQL migrations, leased queue generations, compare-and-swap stage checkpoints, and durable external-effect states (`prepared`, `attempted`, `succeeded`, `indeterminate`).
- **Verification:** Docker is the only verified backend. Containers use a read-only root filesystem, tmpfs workspace, no network, no capabilities, a non-root user, and bounded CPU, memory, PID, and time limits.
- **Authority:** the GitHub App can read pull requests and write PR comments/check runs. It cannot write repository contents, Actions, or issues.
- **Tracing:** the public `traceparent` is discarded. The authenticated edge creates the trusted producer context; the worker verifies and continues that carrier.

See [the architecture guide](docs/architecture.md), [permission contract](docs/permissions.md), and [ADRs](docs/decisions/).

## Why this exists

- **A verifier should arbitrate.** Model output can propose a finding, test, or patch, but it cannot declare its own work correct.
- **Crash safety matters at API boundaries.** A timeout after a GitHub POST is ambiguous. Retrying it can duplicate a check or comment, so `enten` records attempts before the network call and stops on ambiguity.
- **Untrusted PRs need a small boundary.** The release refuses private checkouts, arbitrary webhook commands, host-shell expansion, unsafe paths, symlinks, submodules, secrets, and networked verifier containers.
- **Quality claims need independent inputs.** Stub findings are separate from expected eval labels; missing predictions, zero collected tests, malformed reports, and unavailable scanners fail closed.

## How it works

1. The Hono edge validates required headers and content type before reading a size-limited raw body, verifies the SHA-256 HMAC, validates the strict versioned job contract, and atomically enqueues it.
2. The worker claims a job with a generation and lease, validates the authenticated trace carrier, and fetches the exact public PR ref into a mode-0700 temporary checkout.
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

1. Create a GitHub App from `packages/ts/app/github-app.yml` and install it on a **public test repository**.
2. Copy `.env.example` to `.env`; replace `GITHUB_APP_ID`, `GITHUB_WEBHOOK_SECRET`, and `GITHUB_APP_PRIVATE_KEY_PATH` with real values.
3. Select a trusted verifier image and command. The default `python -m compileall -q .` is a syntax smoke check, not a project test suite.
4. Start the stack:

```bash
scripts/dev-up.sh
curl --fail http://127.0.0.1:3000/health
open http://127.0.0.1:16686
```

For the pinned HTTPie demonstration image:

```bash
docker build -t enten-verifier-httpie:3.2.4 docker/verifier-httpie
# in .env:
# SANDBOX_DOCKER_IMAGE=enten-verifier-httpie:3.2.4
# VERIFICATION_COMMAND=python /opt/verifier/verify.py
```

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
