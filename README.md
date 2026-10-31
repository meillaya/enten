# enten

enten reviews GitHub pull requests in a Docker sandbox and reports a
verdict derived from one command's exit code. It runs as a self-hosted GitHub
App for a team, or as a CLI on a local checkout.

The worker fetches the exact pull request head, runs the operator-selected
verification command three times in a networkless container, and maps the exit
codes to `pass`, `flaky`, `fail`, or `error`. Findings can describe a defect;
they cannot change that verdict. The GitHub App reads pull requests and writes
checks and comments. It cannot write repository contents.

## Quickstart

Requirements: Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).
Node, pnpm, and Docker are only needed for the full development and hosted
paths.

```bash
git clone https://github.com/Meillaya/enten.git
cd enten
uv sync --project packages/python/enten --all-groups --extra test
uv run --project packages/python/enten enten
```

The review takes a few seconds and prints a short result:

```text
PASS  python -m compileall -q .
review <review_id>  .enten/reviews.json
```

The default local command is a fast Python syntax check. Pass `--verify` to
run your own command, or `--verify auto` to detect pytest, package, make, Go,
or Cargo. Add `--json` for machine-readable output. The local path posts
nothing to GitHub.

The operator commands (`worker`, `cost`, `eval`, and `reconcile`) remain
available but are hidden from the top-level help. Use the docs when you need
the worker or a repair command.

## CLI demo

The recording runs a local review, the JSON form, and the top-level help. The
source is
[docs/demo.tape](docs/demo.tape); render it with VHS 0.11.0. VHS 0.12.0
currently drops encoded output.

![CLI demo: local review, JSON output, and help](docs/demo.gif)

## Hosted review

![The verifier exit code decides the verdict](docs/figures/architecture.png)

A signed webhook reaches the TypeScript edge, which validates the HMAC and
enqueues a versioned job in Postgres. The Python worker claims the job with a
lease generation, fetches the exact head SHA from the repository, and
starts the verifier container. The container runs with a read-only root
filesystem and no network. All Linux capabilities are dropped. It runs as a
non-root user and bounds CPU, memory, PID count, and time.

A check and one pull request comment go out through a durable effect ledger.
The worker records an attempt before each GitHub write and stops on an
ambiguous result, so a timeout does not become a duplicate comment. If the
check exists but its response was lost, `enten reconcile` adopts the observed
check and requeues the run; the missing comment is posted once.

The worker uses the bounded local generator by default. Set
`REVIEW_GENERATOR=llm` and configure one provider to let a model propose
findings: `openai-compat` for OpenAI, OpenRouter, and LM Studio, `anthropic`
for the native Messages API, or `ollama` for local models. The verifier exit
code still decides the verdict.

Set `REVIEW_PROBES=on` to publish only findings that a generated pytest probe
reproduces in the sandbox. `shadow` measures probes without publishing. The
same setting applies to private repositories. The verifier image must contain
pytest and the project dependencies.

## Deployment

Compose runs Postgres, migrations, the edge, and the worker on one Docker host.
The worker needs `/var/run/docker.sock`, which is root-equivalent host
authority, so use a dedicated host. The topology, environment variables, and
known limits are in [docs/deploy.md](docs/deploy.md). The full worker setup and
live pull request test are in
[docs/worker-deployment.md](docs/worker-deployment.md).

The web tier and migrations also run on Railway from `.railway/railway.ts`.
Apply it with `railway config apply` on CLI 5.42.1 or newer. Railway does not
provide a Docker socket, so the worker stays on a self-hosted host. The scratch
deployment tested on 2026-10-31 served `/health` and `/ready`, ran the
migrations, and accepted a signed webhook into Postgres.

## Checks

```bash
make check
```

That target runs Ruff, the TypeScript compiler and build, Python and Vitest
coverage gates, the eval harness, the permission check, a gitleaks scan, and a
production dependency audit.

## Documentation

- [Architecture and trust boundaries](docs/architecture.md)
- [Deployment and limitations](docs/deploy.md)
- [Worker deployment and live test](docs/worker-deployment.md)
- [Operations runbook](docs/runbook.md)
- [GitHub App permissions](docs/permissions.md)
- [Verifier demo contracts](docs/demo-repo.md)
- [Audit and fixes](docs/audit.md)
