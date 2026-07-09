# Deploy runbook

This document describes how to build, configure, and deploy the enten
production image.

## Image overview

The production image is built from the repo-root `Dockerfile`:

- **Builder stage** resolves Python dependencies with `uv` and compiles the
  TypeScript workspace with `pnpm`.
- **Runtime stage** copies the virtual environment, the source tree, and the
  built `dist/` artifacts. It runs as UID/GID `1000` (never root).
- **Health check** probes `http://localhost:9090/health` via `health.py`.
- **Ports**: `3000` (webhook HTTP), `9090` (Prometheus metrics, also serves
  `/health`).

## Local build and smoke test

```bash
# Build the image.
docker build -t enten:latest .

# Run a one-off shell to inspect the image (not for production).
docker run --rm -it enten:latest sh

# Run with a local Postgres + Jaeger stack.
scripts/dev-up.sh --with-app
```

## Required secrets

The application will not boot without the following environment variables.
Set them via the platform's secret mechanism — never commit them or bake
them into the image.

| Variable | Purpose | Set via |
|----------|---------|---------|
| `DATABASE_URL` | Postgres DSN (`postgresql+asyncpg://...`) | `fly secrets set` / Railway variables |
| `GITHUB_APP_ID` | GitHub App identifier | `fly secrets set` / Railway variables |
| `GITHUB_WEBHOOK_SECRET` | Webhook signature verification | `fly secrets set` / Railway variables |
| `OPENAI_API_KEY` | LLM provider auth | `fly secrets set` / Railway variables |
| `E2B_API_KEY` | Sandbox provider auth | `fly secrets set` / Railway variables |

## Fly.io

### First-time setup

```bash
# Authenticate (one-time).
flyctl auth login

# Create the app (uses fly.toml at repo root).
flyctl apps create enten-ai

# Set secrets.
flyctl secrets set DATABASE_URL="..." \
                   GITHUB_APP_ID="..." \
                   GITHUB_WEBHOOK_SECRET="..." \
                   OPENAI_API_KEY="..." \
                   E2B_API_KEY="..."
```

### Deploy

```bash
bash scripts/deploy-staging.sh
```

### Scale

```bash
# Vertical scaling: edit [[vm]] size in fly.toml, then redeploy.
# Horizontal scaling: change min_machines_running or use flyctl scale.
flyctl scale count 2
```

## Railway

Railway uses `railway.toml` when present:

- **Build command**: `uv sync`
- **Start command**: `uv run enten worker & node packages/ts/app/dist/server.js & wait`
- **Health check**: `GET /health` with a 30-second timeout

Set the same secrets in Railway's dashboard or CLI, then push to the
connected branch to trigger a deploy.

## Docker Compose (local / QA)

```bash
# Default: Postgres + Jaeger only.
docker compose up -d

# With the production app image.
docker compose --profile app up -d
```

The `app` service builds the Dockerfile and depends on `postgres` and
`jaeger`.

## Health checks

- Container health check: `python /app/health.py` every 30 s.
- Fly.io HTTP check: `GET /health` on port 3000 every 30 s with a 60 s
  grace period.
- Railway health check: `GET /health` with a 30 s timeout.

## Troubleshooting

- **Image fails to build**: verify `uv.lock`, `pnpm-lock.yaml`, and
  `pyproject.toml` are committed and not stale.
- **Container exits on boot**: check that all required secrets are set.
  Missing `DATABASE_URL` or `GITHUB_WEBHOOK_SECRET` is the most common
  cause.
- **Health check fails**: confirm `METRICS_PORT=9090` and that the TS
  server is listening. The Hono app serves `/health` on both ports.
- **Permission audit fails**: run `bash scripts/check-permissions.sh`.
  It forbids `contents: write` and `actions: write` in deployment
  configuration.
