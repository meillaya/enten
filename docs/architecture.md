# Architecture

`enten` is a queue-driven, self-hosted pull-request reviewer with an explicit trust boundary between authenticated orchestration and untrusted repository content.

## Request and job path

```text
GitHub
  │ POST /webhook (signed, size bounded)
  ▼
TS edge: receive → authenticate → normalize → enqueue
  │ strict github-pr-review-job.v1 contract + trusted trace carrier
  ▼
Postgres: jobs → leased claim generation → review checkpoints
  ▼
Python worker: validate trace → exact public checkout → shared pipeline
  │                                      │
  │                                      └─ Docker sandbox: verifier exit code
  ▼
external_effects: prepare → attempted → succeeded | indeterminate
  ▼
GitHub Check Run, then PR comment
```

The web and metrics listeners are separate. The TypeScript process starts OpenTelemetry before constructing its apps and listeners, and closes listeners, Postgres, and OTel on shutdown. The Python worker has a separate process role and finishes its current claim before shutdown.

## Trust boundaries

### Webhook edge

The handler validates the signature header and `application/json` before reading the request, enforces the body limit, verifies the raw-body HMAC once, and then validates the event and delivery headers before enqueueing. Only these events create work:

- `pull_request.opened`
- `pull_request.reopened`
- `pull_request.synchronize`
- `pull_request.ready_for_review` when the PR is not a draft

Unsupported actions and events return an acknowledgement without enqueueing. A malformed supported event is rejected. GitHub's inbound trace headers are untrusted; the authenticated handler creates the producer trace and stores its W3C carrier with the job.

### Repository checkout

The release accepts public GitHub repositories only. The worker:

1. derives `https://github.com/<owner>/<repo>.git` from the validated full name;
2. disables interactive credentials and global Git configuration;
3. fetches only `refs/pull/<number>/head`, with no tags and depth one;
4. checks out detached and requires `HEAD == head_sha`;
5. revalidates the canonical origin and remote PR ref before execution;
6. deletes the mode-0700 temporary checkout in a shielded cleanup block.

Private repositories, arbitrary clone URLs, symlinks, submodules, binary payloads, unsafe relative paths, and repository secrets are outside this release boundary.

### Verifier transport and resource contract

Docker is the only verified backend. The worker collects tracked and unignored text files, rejects unsafe paths, and excludes symlinks, binary files, repository metadata, dependency/build directories, and secret-shaped filenames. Input is bounded to 2,000 files, 2 MiB per file, and 64 MiB total before transport. The worker creates a normalized tar stream with relative POSIX paths, mode `0644`, and uid/gid `1000`, then sends it through the Docker exec socket into `/workspace`. No worker-local checkout path is bind-mounted into the verifier.

The verifier uses a digest-pinned operator-selected image, uid/gid `1000`, a read-only root filesystem, no network, no Linux capabilities, and `no-new-privileges`. `/workspace` is a 512 MiB tmpfs, `/tmp` is a 128 MiB `noexec` tmpfs, and the container is limited to 256 PIDs, 4 GiB memory, and two CPUs. A command defaults to 120 seconds and cannot request more than 600 seconds; combined stdout/stderr is capped at 8 MiB. The verifier command comes from operator configuration, not webhook input, and is executed as argv without a shell. Containers carry `enten.sandbox=true` for ownership-aware cleanup. E2B selection raises an actionable unavailable-backend error before network activity.

## Durable state

### Migrations

SQL files are ordered and recorded by raw-byte SHA-256 checksum. One advisory lock serializes migration application. All pending migrations apply transactionally, exact baselines are verified before adoption, and drift is an error. Released migrations are immutable; schema repair is forward-only.

### Queue and checkpoints

`claim_next` uses `FOR UPDATE SKIP LOCKED`, increments `claim_generation`, and sets a lease. Every state transition and review checkpoint is compare-and-swap fenced by that generation and an unexpired lease. A stale worker cannot overwrite a replacement worker's progress.

The canonical `ReviewRun` records request identity, proposal evidence, findings, verifier exits, verdict, cost fields, and report receipt. Local CLI runs use an atomic JSON store; production jobs use Postgres checkpoints.

### External effects

Checks and comments use separate effect keys. The worker persists the deterministic request and marker, records `attempted`, calls GitHub, and then stores the external ID. A previous `attempted` row without a durable receipt becomes `indeterminate`; it is not automatically retried. This trades availability for duplicate-write safety.

## Pipeline and verdict

The shared pipeline stages are:

```text
created → proposed → findings → verified → persisted → reporting → completed
```

Queue states map those stages to `indexing`, `reviewing`, `testing`, `verifying`, `commenting`, and `done`. The default generator emits bounded repository-anchored local findings. LLM provider, prompt, cost, and eval components are available to explicit callers, but are not silently activated by the production worker.

The verifier exit code is authoritative across the default three fresh runs:

- `3/3` runs exit `0` → `pass`;
- `2/3` runs exit `0` → `flaky`;
- `1/3` or `0/3` runs exit `0` → `fail`;
- infrastructure/parser failure → `error`.

Reports may describe findings, but findings cannot change this result.

## Observability

The causal trace includes a `POST /webhook` server span containing authentication, a `jobs enqueue` producer span, worker consume, and pipeline reserve/propose/findings/verify/persist/report spans. The worker rejects a missing, malformed, or trace-ID-mismatched carrier before pipeline execution. Both processes export traces to the local OTLP target; the TypeScript edge alone serves the bounded Prometheus metrics endpoint on `METRICS_PORT`.

## Release topology

Compose separates `migrate`, `web`, and `worker`. Postgres and Jaeger are loopback-bound. The worker receives the GitHub PEM as a read-only bind and the Docker socket with its numeric group ID. Docker socket access is effectively host authority, so the verified topology is a dedicated self-hosted worker. Fly.io and Railway files demonstrate process separation but are not evidence that the Docker verifier runs on those hosted platforms.

## Related decisions

See [`docs/decisions/`](decisions/) for the language split, Postgres queue, pgvector, cost model, determinism boundary, permissions, prompt-injection controls, checksum migrations, authenticated ingress/tracing, sandbox backend, and crash-fenced effects.
