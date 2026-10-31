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

### 8. The runtime image could not check out a repository

The first live worker run failed at checkout. The runtime image had `prlimit`
but no `git`, because the slim Python base does not include it and the
Dockerfile never installed it. The worker could not run `git init` or fetch the
pull request ref.

The runtime stage now installs `ca-certificates` and `git` with apt. A Dockerfile
test checks that `git` and `prlimit` resolve before the image ships. The next
live run completed the full path.

## Reviewed and held

- Webhook ingress reads a size-capped raw body, checks the content type,
  verifies the HMAC over that body, then validates the event schema. GitHub
  documents the same SHA-256 signature check:
  https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
- The App manifest requests `metadata: read`, `pull_requests: write`, and
  `checks: write`. It has no `contents` or `actions` permission. The parser test
  compares the exact set.
- Public checkout uses the canonical HTTPS URL, disables credential helpers,
  fetches the exact requested head SHA, requires `HEAD == head_sha`, rejects
  symlinks and submodules through the tree mode check, and enforces file count
  and byte quotas before execution. Fetching the exact SHA keeps a retry valid
  after the author pushes another commit to the PR.
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

## Deployment tests

### Railway web tier

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

### Full VM path

The Compose stack ran on a machine0 VM with a real GitHub App installed on
`meillaya/enten-test-repo`. Pull request
[#1](https://github.com/meillaya/enten-test-repo/pull/1) exercised both
verdicts.

- Passing head `bbac889`: job `done`, Check Run `success`, comment `pass`,
  verification exits `[0, 0, 0]`.
- Failing head `43b5268`: job `done`, Check Run `failure`, comment `fail`,
  verification exits `[1, 1, 1]`.
- `external_effects`: the check and comment rows reached `succeeded` for both
  runs.
- Trace `42e7eab4f29d21bcecaab8b9b5869957`: 64 spans, including
  `POST /webhook`, `jobs enqueue`, `pr_review.consume`, and the pipeline stages.
- The public HTTPS origin served `/health` and `/ready`. `/metrics` stayed
  private.
- Migration `0004_effect_reconciliation.sql` applied before the final matrix.

### Full matrix on the test image

The final image ran against GitHub App `5557296` on
`meillaya/enten-test-repo`. The LLM path used OpenRouter with
`deepseek/deepseek-v4.1-flash-1017`. Eight calls cost `$0.000320`.

- LLM pass: head `0a31bdf`, check `124620650964` (`success`), comment
  `6547440770`, exits `[0, 0, 0]`. The comment contained findings for
  `review_target.py:2`.
- LLM fail: head `22c8a34`, check `124621047563` (`failure`), exits
  `[1, 1, 1]`. The comment reported the `eval` finding and the syntax error in
  `broken_target.py`.
- Flaky: head `5d8bb4f`, check `124615550578` (`neutral`), exits `[0, 1, 0]`.
- Error: head `ed78988`, check `124615741365` (`failure`), exits `[]`, comment
  reported an infrastructure error.
- Crash recovery: the worker was killed while a job held a lease. After the
  lease expired, a restarted worker reclaimed the job at generation 3 and
  finished it (`pr_kywzdiqrgeze`, attempts 2).
- Indeterminate check: a fault proxy forwarded the check POST to GitHub and
  returned 503. The check existed (`124620650964`), the ledger recorded
  `github-check=indeterminate`, and the job failed without a duplicate write.
- Reconciliation: `enten reconcile --run-id pr_iyiekumniw6d --check-id
  124620650964` verified the head and App, adopted the check, and requeued the
  job. The worker posted the missing comment `6547440770` and marked the job
  `done`. GitHub held exactly one check for the head.
- Python tests, Ruff, Vitest, and the TypeScript build passed on the final
  tree.

## Residual risk

- The native Anthropic Messages client is unit tested but not live tested. The
  configured Anthropic key was empty. The OpenAI-compatible path was live
  tested through OpenRouter.
- GitHub Actions quota was exhausted on the test account, so the CI workflow
  could not run in that account. The same gates passed locally.
- Reconciliation is operator-initiated. The worker stops safely on an
  ambiguous effect and never adopts a receipt without an observed external ID.
- The exact-SHA retry change was made after the live matrix. Unit tests cover
  the fetch and validator path, but a live retry across a new push was not
  repeated.
- The worker cannot run on Railway. A live verification test needs a Docker
  host, and Docker socket access is root-equivalent authority.
- The default verifier runs `python -m compileall -q .`, which checks Python
  syntax only. Operators must set a real test command and a digest-pinned
  image for meaningful results.
- The local generator remains the default. The LLM generator is opt-in with
  `REVIEW_GENERATOR=llm`, and the live matrix must cover its failures as well
  as its findings.
- Ten moderate dependency advisories remain in the production and development
  trees. None is high or critical. `make audit` gates production high and
  critical findings, and `make audit-dev` reports the full tree.
