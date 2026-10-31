# Architecture

enten runs signed GitHub pull requests through a TypeScript edge, a Postgres
queue, and a Python worker. The worker verifies the exact public head in a
Docker sandbox. Orchestration and repository content sit on opposite sides of a
trust boundary.

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

The web and metrics listeners use separate Hono apps. The TypeScript process
starts OpenTelemetry before it constructs the apps and listeners. On shutdown it
closes the listeners, Postgres, and OpenTelemetry. The Python worker runs in a
separate process. Compose gives it 15 minutes to finish the current bounded
claim. A supervisor that force-kills it leaves a reclaimable lease, and
generation fencing makes the claim retryable.

## Trust boundaries

### Webhook edge

The handler checks the signature header and `application/json` before it reads
the request. It enforces the body limit, verifies the raw-body HMAC once, and
validates the event and delivery headers before enqueueing. These events create
work:

- `pull_request.opened`
- `pull_request.reopened`
- `pull_request.synchronize`
- `pull_request.ready_for_review` when the pull request is not a draft

Unsupported actions and events return an acknowledgement without a job. A
malformed supported event is rejected. The edge discards GitHub's inbound trace
headers. After authentication it creates the producer trace and stores the W3C
carrier with the job.

### Repository checkout

The release accepts public GitHub repositories. The worker:

1. derives `https://github.com/<owner>/<repo>.git` from the validated full name;
2. disables interactive credentials and global Git configuration;
3. fetches only `refs/pull/<number>/head`, with no tags and depth one;
4. checks out detached and requires `HEAD == head_sha`;
5. revalidates the canonical origin and remote pull request ref before
   execution;
6. deletes the mode-0700 temporary checkout in a shielded cleanup block.

Private repositories, arbitrary clone URLs, symlinks, submodules, binary
payloads, unsafe paths, and repository secrets stay outside the release
boundary.

### Verifier transport and resource contract

Docker is the only verified backend. The worker collects tracked, unignored
text files. It rejects unsafe paths and excludes symlinks, binary files,
repository metadata, build directories, and secret-shaped names. Input is
bounded to 2,000 files, 2 MiB per file, and 64 MiB total. The worker builds a
normalized tar stream with relative POSIX paths, mode `0644`, and uid/gid
`1000`. It sends that stream through the Docker exec socket into `/workspace`.
It does not bind the checkout into the verifier.

The verifier uses a digest-pinned image chosen by the operator. It runs as
uid/gid `1000` with a read-only root filesystem and no network. All Linux
capabilities are dropped, and `no-new-privileges` is set. `/workspace` is a
512 MiB tmpfs and `/tmp` is a 128 MiB `noexec` tmpfs. The container has 256
PIDs, 4 GiB memory, and two CPUs. A command defaults to 120 seconds and cannot
exceed 600 seconds. Combined stdout and stderr is capped at 8 MiB. The command
comes from operator configuration and runs as argv without a shell. Containers
carry `enten.sandbox=true` for cleanup. Selecting E2B raises an
unavailable-backend error before any network call.

## Durable state

### Migrations

SQL files are ordered and recorded by raw-byte SHA-256 checksum. One advisory
lock serializes application. All pending migrations apply in one transaction.
An exact baseline is verified before adoption, and drift is an error. Released
migrations are immutable, and schema repair moves forward.

### Queue and checkpoints

`claim_next` uses `FOR UPDATE SKIP LOCKED`. It increments `claim_generation` and
sets a lease. Every state transition and review checkpoint is compare-and-swap
fenced by that generation and an unexpired lease. A stale worker cannot
overwrite a replacement worker's progress.

`ReviewRun` records request identity, proposal evidence, findings, verifier
exits, verdict, cost fields, and the report receipt. The local CLI uses an
atomic JSON store. Production jobs use Postgres checkpoints.

### External effects

Checks and comments use separate effect keys. The worker persists the
deterministic request and marker, records `attempted`, calls GitHub, and stores
the external ID. An `attempted` row without a durable receipt becomes
`indeterminate` and is not retried automatically. The trade is availability for
duplicate-write safety.

## Pipeline and verdict

```text
created → proposed → findings → verified → persisted → reporting → completed
```

Queue states map those stages to `indexing`, `reviewing`, `testing`,
`verifying`, `commenting`, and `done`. `REVIEW_GENERATOR` selects the findings
generator. `local` emits bounded findings anchored in the repository. `llm`
uses the configured OpenAI-compatible, Anthropic, or Ollama provider. The
verifier exit code remains authoritative either way.

The verifier exit code is authoritative over three fresh runs:

- `3/3` runs exit `0` gives `pass`;
- `2/3` runs exit `0` gives `flaky`;
- `1/3` or `0/3` runs exit `0` gives `fail`;
- an infrastructure or parser failure gives `error`.

Reports can describe findings. Findings cannot change the result.

## Observability

The trace contains a `POST /webhook` server span, a `jobs enqueue` producer
span, a worker consume span, and pipeline reserve, propose, findings, verify,
persist, and report spans. The worker rejects a missing, malformed, or
trace-ID-mismatched carrier before the pipeline runs. Both processes export
traces to the OTLP target. The TypeScript edge serves the bounded Prometheus
metrics on `METRICS_PORT`.

## Release topology

Compose separates `migrate`, `web`, and `worker`. Postgres and Jaeger bind to
loopback. The worker receives the GitHub PEM as a read-only bind and the Docker
socket with its numeric group ID. Docker socket access is effectively host
authority, so the verified topology uses a dedicated self-hosted worker. The
Railway IaC file runs the web tier and migrations. Railway has no Docker
socket, so the verifier cannot run there. `fly.toml` remains a process topology
template with no live deployment.
