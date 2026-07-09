# Demo repository

> Status: demo repo selected (`httpie/cli`), fixture corpus scaffolded.
> Real fixtures and eval harness arrive in T36/T37.

The demo repository is a small, known open-source project that the agent will
be pointed at to exercise every end-to-end capability. It exists so that:

1. CI can run reproducible smoke tests against a real GitHub-shaped payload.
2. The eval harness can score completions against known-good bug-fix PRs.
3. Humans can poke the agent in a sandboxed way during development.

For this project we selected the public **`httpie/cli`** repository.

## Why this repo

`httpie/cli` is the upstream home of HTTPie, a command-line HTTP client. It is
a good demo target for enten because:

- **Small enough to reason about** — the core CLI package is roughly 30 kLOC of
  Python, so the agent can ingest the whole repo context without hitting token
  cliffs.
- **Python + CLI** — matches the agent's primary language (Python) and a
  well-understood domain (CLI argument parsing, HTTP requests, output
  formatting).
- **Mature test suite** — the project uses `pytest`, so the agent can run
  existing tests to validate patches and we can write fixture replays that
  exercise real test failures.
- **Permissive license** — BSD-3-Clause, which is safe to fork, mirror, and
  redistribute as a fixture.
- **Known bug-fix history** — the project has a long history of small,
  well-scoped bug-fix PRs (off-by-one formatting issues, null-deref guards,
  input validation holes) that are ideal for replay fixtures.
- **No real user data** — a public OSS repo; fixtures can be derived entirely
  from public commits and issues.

## How to fork (one-time manual step)

CI cannot create the fork, so a human with GitHub CLI access does it once:

```bash
gh repo fork httpie/cli --clone=false --remote=true
```

This creates a fork under your personal/org account without cloning it
locally. The bot will be installed against that fork, not against the upstream
repo, so tests cannot accidentally spam the real HTTPie maintainers.

## How to obtain a GitHub App installation URL

1. Create a GitHub App (or reuse the enten app) with the permissions listed in
   `docs/github-app.md` (minimum: `pull_requests: write`, `checks: write`,
   `metadata: read`; subscribe to `pull_request`, `pull_request_review`, and
   `issue_comment` events).
2. From the app's settings page, choose **Install App** and select the forked
   `httpie/cli` repository.
3. Copy the installation URL (e.g.
   `https://github.com/apps/<app-name>/installations/new`) and set it in the
   environment as `GITHUB_APP_INSTALLATION_URL` if your bootstrap flow needs it.

T33's bootstrap task documents how the App ID, private key, and webhook secret
are wired into `Settings`.

## Demo flow

The canonical smoke test is:

1. Open a PR on the forked `httpie/cli` repo that contains a small, known bug
   (for example, a fixture from `tests/fixtures/prs/bug/off_by_one/`).
2. The GitHub webhook delivers the `pull_request` event to the enten edge
   running on `localhost:3000` (or the deployed endpoint).
3. The worker claims the job, runs the agent pipeline, and posts a review
   comment.
4. Within two minutes the bot comment appears on the PR with a patch or
   feedback.

T16 will run the indexer against this fork, and T36 will populate the fixture
corpus with real PR shapes derived from public `httpie/cli` history.

## Local stack

The Postgres + Jaeger backing services live in
[`docker-compose.yml`](../docker-compose.yml). Two helper scripts wrap the
common cases:

```bash
scripts/dev-up.sh              # start postgres + jaeger; wait for healthy
scripts/dev-down.sh            # stop everything; drop the pgdata volume
scripts/dev-up.sh --with-dev   # also apply docker-compose.dev.yml
                               # (bind-mount the repo into the `app` service
                               # for live reload once T40 adds the real image)
scripts/dev-up.sh --with-ollama  # add Ollama (local LLM backend) under the
                                 # `ollama` profile
```

Endpoints exposed by the default stack:

| Service  | URL                                              | Notes                       |
|----------|--------------------------------------------------|-----------------------------|
| Postgres | `postgresql://enten:enten@localhost:5432/enten`  | `pgvector` extension enabled|
| Jaeger   | http://localhost:16686                           | UI + `/api/services` JSON   |
| OTLP     | http://localhost:4317 (gRPC) · :4318 (HTTP)      | OTel SDK export target      |

The `app` service is a busybox placeholder; the real image arrives in T40.
Until then the dev stack is just the persistence + observability backbone that
Alembic (T08) and the OTel SDK (T05) plug into.

## Fixture corpus layout

PR-shaped fixtures live under `tests/fixtures/prs/`:

```
tests/fixtures/prs/
├── bug/
│   ├── off_by_one/
│   ├── null_deref/
│   └── missing_validation/
├── no_false_positive/
│   ├── clean_format/
│   └── refactor_only/
├── missing_test/
│   ├── feature_no_test/
│   └── edge_case/
├── security/
│   ├── sql_injection/
│   └── hardcoded_secret/
└── prompt_injection/
    └── ignore_previous/
```

Each leaf directory will eventually contain a `pr.json` plus optional diff
artefacts. T36 fills in the actual content; Task 15 only scaffolds the
directories.

See also `architecture.md` for the broader system shape.
