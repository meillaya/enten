# Deployment topology and limitations

## Verified topology

The supported release topology is a dedicated self-hosted Linux Docker host:

- loopback-bound Postgres/pgvector and Jaeger;
- one-shot checksum migration role;
- separate TypeScript web and Python worker processes;
- a read-only GitHub App PEM bind;
- host Docker socket access for the worker, with the socket's numeric group ID;
- an operator-selected immutable verifier image and argv-only command.

```bash
cp .env.example .env
# Replace every GitHub placeholder and select the verifier image/command.
scripts/dev-up.sh
scripts/healthcheck-all.sh
```

`dev-up.sh` fails before starting the full stack when the socket, App ID, webhook secret, or PEM file is missing. Use `--infra-only` when GitHub credentials are intentionally unavailable.

## Security warning

Docker socket access is effectively root-equivalent host authority. A dedicated worker host, restricted administrator access, an immutable verifier image allowlist, and ownership-aware cleanup are required. Do not expose the Docker API over unauthenticated TCP and do not run global prune commands on shared hosts.

## Migrations

```bash
scripts/db-migrate.sh
```

The runner serializes with an advisory lock, verifies raw-byte checksums, and applies all pending files transactionally. Never edit a recorded migration or run destructive down migrations against released data; repair forward.

## Fly.io and Railway

`fly.toml`, `railway.toml`, and `railway.worker.toml` are process-topology templates: they separate release migration, web, and worker commands. They have not been live deployed by this release, and a hosted worker without a supported Docker daemon/socket cannot run the verified sandbox. Do not interpret these files as deployment evidence.

E2B is not a fallback: selecting it fails closed until an official SDK-backed implementation and dependency decision are approved.

## Pre-release rehearsal

```bash
make check
docker build -t enten:release-candidate .
DOCKER_GID=$(stat -c '%g' /var/run/docker.sock) docker compose config --quiet
bash scripts/check-release-secrets.sh
```

Run live webhook/reporting tests only in a disposable public repository with a least-privilege App. Record external limitations instead of substituting mocks for a live deployment claim.
