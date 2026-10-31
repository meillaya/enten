# Audit and fixes

I audited commit `6f759db` on 2026-10-31. The review covered the documented
local path, both test suites, a production image build with Podman 5.8.7, and a
Railway deployment in a scratch project. Docker was not installed on the audit
host; Podman supplied the Docker API through its socket.

## Defects fixed

### 1. A clean install could not build the Python package

`make install` failed before installing dependencies:

```text
ValueError: Readme path must be within the project directory: ../../../README.md
```

`packages/python/enten/pyproject.toml` pointed hatchling at the repository
README, which sits outside the package. Hatchling rejects that path. A clean
clone, the Docker builder, and the Railway build failed the same way. The
package now carries an inline readme value.

### 2. The production image could not load the edge dependencies

The first Railway deployment started and crashed:

```text
Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@hono/node-server'
imported from /app/packages/ts/app/dist/server.js
```

The Dockerfile ran `pnpm prune --prod` at the workspace root, then copied only
`packages/ts/app/node_modules`. A pnpm workspace keeps the real packages in the
root `node_modules/.pnpm` store, and the prune step left the package directory
with no dependency links. The image now builds a self-contained production
package with `pnpm --filter @enten/app deploy --prod --legacy` and copies that
directory. `tests/test_dockerfile.py` imports a production dependency inside
the built image, so the next regression fails in CI instead of production. The
command follows the pnpm deploy contract: https://pnpm.io/cli/deploy.

### 3. Railway never ran the pre-deploy migration

After the web process came up, `/ready` returned 200 and a signed webhook
returned 503. The database had no `jobs` table, and the edge logged
`PostgresError 42P01`. The effective Railway deploy manifest reported
`preDeployCommand: null`.

Railway config-as-code is deprecated, with a hard cutoff on 2026-12-01:
https://docs.railway.com/config-as-code. The replacement is project-level IaC in
`.railway/railway.ts`: https://docs.railway.com/infrastructure-as-code. The
repository now uses that file for the start command, the `/ready` healthcheck,
the pre-deploy migration, and the Postgres variable reference. The next
deployment logged `applied: 0001_init.sql, 0002_pgvector.sql,
0003_crash_consistency.sql`. The runtime contract is documented at
https://docs.railway.com/deployments/pre-deploy-command.

### 4. The public webhook port served Prometheus metrics

`createApp` mounted `/metrics` on the same Hono app that `start` passed to both
listeners, so the public webhook origin exposed the scrape route. The
architecture guide claimed the listeners were separate. The public app now
serves `/health`, `/ready`, and `/webhook`; a second app serves `/metrics` and
`/health` on `METRICS_PORT`. A test asserts that the public app returns 404 for
`/metrics`, and the deployed public origin returns 404.

### 5. Readiness did not check Postgres

`/health` returned a constant JSON body. Railway marked the deployment online
while the queue schema was missing and every webhook returned 503. The edge now
has `/ready`, which runs `SELECT 1` through the queue connection with the
existing statement timeout and returns 503 on failure. The Railway healthcheck
uses `/ready`, as described at
https://docs.railway.com/deployments/healthchecks. `/health` remains a
liveness probe that does not depend on the database.

### 6. The local suite failed without Docker and gitleaks

`make test` raised `FileNotFoundError` for `/var/run/docker.sock` and for the
`gitleaks` binary. The Compose contract tests now skip when the Docker CLI or
socket is absent, and the fixture secret test skips when gitleaks is absent.
`make security` remains the release gate for the secret scan.

### 7. The shipped dependency tree had high-severity advisories

`pnpm audit --prod --audit-level=high` failed on the release tree: 32
advisories, including seven high-severity findings in `brace-expansion`,
`@grpc/grpc-js`, `zod`, and `systeminformation`. The webhook dependencies also
had moderate findings in `hono` and `@hono/node-server`.

The edge now pins `hono` 4.13.14, `@hono/node-server` 2.1.5, and `zod` 4.6.7.
`pnpm-workspace.yaml` overrides `@grpc/grpc-js`, `systeminformation`, and the
`brace-expansion` 2.x line to patched releases. `make audit` now gates on high
and critical findings in production dependencies, because the image ships the
`pnpm deploy --prod` tree. The same command reports 10 moderate advisories and
no high or critical ones.

The development tree had the same high and critical findings through its Vitest
3 test runner. Vitest 5 removed them. Its V8 provider also measured branches
differently: the original suite covered 74.59 percent of branches, below the 80
percent gate. The suite now covers 91.37 percent of branches, so the upgrade
ships with the new tests. `make audit-dev` reports the development tree without
gating a release.

## Reviewed and held

- Webhook ingress reads a size-capped raw body, checks the content type,
  verifies the HMAC over that body, then validates the event schema. GitHub
  documents the same SHA-256 signature check:
  https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
- The App manifest requests `metadata: read`, `pull_requests: write`, and
  `checks: write`. It has no `contents` or `actions` permission. The parser test
  compares the exact set.
- Public checkout uses the canonical HTTPS URL, disables credential helpers,
  fetches only `refs/pull/<n>/head`, requires `HEAD == head_sha`, rejects
  symlinks and submodules through the tree mode check, and enforces file count
  and byte quotas before execution.
- The Docker verifier uses a digest-pinned image, a read-only root filesystem,
  `network=none`, `cap_drop: ALL`, `no-new-privileges`, a non-root user, and
  CPU, memory, PID, time, and output caps. The defaults match the Docker
  hardening guidance: https://docs.docker.com/engine/security/
- The queue uses `FOR UPDATE SKIP LOCKED`, generation fencing, lease renewal,
  and a state-guarded effect ledger. The locking clause follows the documented
  PostgreSQL queue pattern:
  https://www.postgresql.org/docs/current/sql-select.html#SQL-FOR-UPDATE-SHARE
- PR comment rendering escapes HTML, backticks, and `@` mentions, and truncates
  the body at 65,535 characters.

## Deployment test

The scratch Railway project ran the web tier and Postgres. The worker was not
deployed because Railway has no Docker socket.

- Production image build: passed locally before the dependency upgrades and on
  Railway for the final tree.
- Pre-deploy migration: applied `0001_init.sql`, `0002_pgvector.sql`, and
  `0003_crash_consistency.sql`.
- `/health`: 200.
- `/ready`: 200.
- `/metrics` on the public origin: 404.
- Invalid HMAC: 401.
- Signed `pull_request.opened` webhook: 202 with a `run_id`.
- Replayed payload with a fresh delivery ID: 202 with `inserted: false`, and
  the database held one queued job.

The final deploy after the dependency upgrades repeated the health, readiness,
metrics, and webhook checks.

## Residual risk

- The live GitHub App installation and write path were not exercised. No App
  credentials were available, so Check Run and comment posting remain covered
  by contract tests only.
- The worker cannot run on Railway. A live verification test needs a Docker
  host, and Docker socket access is root-equivalent authority.
- The default verifier runs `python -m compileall -q .`, which checks Python
  syntax only. Operators must set a real test command and a digest-pinned
  image for meaningful results.
- The default worker makes no model calls. The OpenAI-compatible and Ollama
  clients are library code and are not wired into the production worker.
- Ten moderate dependency advisories remain in the production and development
  trees. None is high or critical. `make audit` gates production high and
  critical findings, and `make audit-dev` reports the full tree.
