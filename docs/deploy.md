# Deployment topology and limits

## Verified topology

The supported release runs on a dedicated self-hosted Linux Docker host:

- loopback-bound Postgres/pgvector and Jaeger;
- one-shot checksum migration role;
- separate TypeScript web and Python worker processes;
- a read-only GitHub App PEM bind;
- host Docker socket access for the worker, with the socket's numeric group ID;
- an operator-selected immutable verifier image and argv-only command.

```bash
cp .env.example .env
# Replace every GitHub placeholder and select the verifier image and command.
scripts/dev-up.sh
scripts/healthcheck-all.sh
```

`dev-up.sh` fails before it starts the full stack when the socket, App ID,
webhook secret, or PEM file is missing. Use `--infra-only` when GitHub
credentials are unavailable.

`healthcheck-all.sh` is a runtime probe. It requires `postgres`, `jaeger`,
`web`, and `worker` to run, checks Postgres readiness, and calls the edge health
endpoint. `make check` is the separate static release gate. It does not prove
that a deployed stack is healthy.

## Security warning

Docker socket access is effectively root-equivalent host authority. Use a
dedicated worker host, restrict administrator access, allowlist the verifier
images, and clean up only project-owned containers. Do not expose the Docker
API over unauthenticated TCP. Do not run global prune commands on shared hosts.

## Migrations

```bash
scripts/db-migrate.sh
```

The runner takes an advisory lock, verifies raw-byte checksums, and applies all
pending files in one transaction. Released migrations are immutable. Repair
forward instead of running destructive down migrations against released data.

## Worker deployment

The verified worker needs a Docker daemon and the GitHub App private key. Run it
on the same Docker host as Postgres and the edge with the Compose topology
above. The full sequence, including the GitHub App setup and the live pull
request check, is in [worker-deployment.md](worker-deployment.md).

Railway and Fly.io cannot run the verified worker because neither provides a
Docker daemon. A worker service on those platforms would start and fail at the
first verification step.

## Railway

The web tier and migrations run on Railway from `.railway/railway.ts`. The
scratch deployment on 2026-10-31 ran the checksum migrations, served `/health`
and `/ready`, and accepted a signed webhook into Postgres. Railway provides
private networking between the edge and Postgres. The service reads the
database URL from the `Postgres` service reference.

Apply the file with `railway config apply`. The IaC commands need Railway CLI
5.42.1 or newer. The file sets the migration pre-deploy, the `/ready`
healthcheck, and the container start command. The GitHub App values use
`preserve()`, so they stay in Railway instead of source. Set them with
`railway variable set` before the first apply.

The tested boundary is the web tier and queue. Verification and GitHub
reporting need a self-hosted worker.

## Fly.io

`fly.toml` is a process-topology template with no live deployment. A hosted
worker without a Docker daemon cannot run the verified sandbox.
`scripts/deploy-staging.sh --check-templates` checks the static command
boundaries. Running the script without that option refuses deployment instead
of invoking a provider CLI.

E2B is not a fallback. Selecting it fails closed until an approved SDK
implementation, dependency decision, and equivalent lifecycle tests exist.

## Pre-release rehearsal

```bash
make check
docker build -t enten:release-candidate .
DOCKER_GID=$(stat -c '%g' /var/run/docker.sock) docker compose config --quiet
bash scripts/check-release-secrets.sh
```

Run live webhook and reporting tests in a disposable public repository with a
least-privilege App. Record the limits of that test. Do not present a mock run
as a live deployment.
