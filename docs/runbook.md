# Operations runbook

Commands assume the repository root and the Compose project name `enten`. Never use global Docker or volume prune on a shared host.

## Webhook is not enqueueing jobs

1. Inspect edge health and logs:

   ```bash
   curl --fail http://127.0.0.1:3000/health
   docker compose logs --tail 200 web
   ```

2. Inspect recent queue state:

   ```bash
   docker compose exec -T postgres psql -U enten -d enten -c \
     "SELECT state, count(*) FROM jobs WHERE created_at > now() - interval '1 hour' GROUP BY state ORDER BY state"
   ```

3. In GitHub, check the delivery response:
   - `401`: webhook secret/signature mismatch;
   - `413`: body exceeds the configured limit;
   - `415`: not `application/json`;
   - `422`: malformed supported payload;
   - `503`: bounded enqueue deadline or database failure;
   - `200 ignored`: unsupported event/action or draft PR.

Rotate a compromised webhook secret in GitHub and the deployment secret store, restart `web`, then redeliver a non-sensitive recent event.

## Worker is not claiming

```bash
docker compose logs --tail 200 worker
docker compose ps
docker compose exec -T postgres psql -U enten -d enten -c \
  "SELECT run_id,state,attempts,claimed_by,claim_generation,lease_expires_at,last_error FROM jobs ORDER BY created_at DESC LIMIT 20"
```

The worker intentionally refuses to start without a real App ID and readable PEM. It also needs the Docker socket and correct supplemental group. Check:

```bash
stat -c '%g %a %U:%G' /var/run/docker.sock
docker compose config --format json | jq '.services.worker | {group_add,volumes,environment}'
```

Restarting the worker is safe: expired leases are reclaimed with a new generation, and stale generations cannot update checkpoints.

## Verification failures

A nonzero candidate exit is a product verdict, not an infrastructure incident. An `error` indicates checkout, sandbox, parser, scanner, or configuration failure.

```bash
docker compose logs --tail 300 worker
docker ps -a --filter label=enten.sandbox=true \
  --format 'table {{.ID}}\t{{.Image}}\t{{.Status}}'
```

Confirm `SANDBOX_DOCKER_IMAGE` exists on the worker host and `VERIFICATION_COMMAND` is valid for that image. The default compileall command is only a syntax smoke check. E2B is unsupported and must not be selected as an emergency fallback.

## Indeterminate GitHub effect

An effect becomes `indeterminate` when a GitHub request may have succeeded but its receipt is not durable. Automatic retry is disabled to prevent duplicates.

1. Stop the worker.
2. Query the effect and matching checkpoints:

   ```bash
   docker compose exec -T postgres psql -U enten -d enten -c \
    "SELECT run_id,effect_key,state,marker,external_id,last_error FROM external_effects WHERE state='indeterminate' ORDER BY COALESCE(completed_at,attempted_at,prepared_at) DESC"
   ```

3. Use the marker and reviewed SHA to inspect existing GitHub Checks/comments.
4. Reconcile with a forward repair that records the observed external ID or creates a new explicitly versioned effect key. Never change `indeterminate` back to `prepared` blindly.
5. Restart the worker after reconciliation.

## Migration drift or baseline failure

```bash
scripts/db-migrate.sh
```

The idempotent apply command validates every recorded filename and raw-byte checksum before applying a contiguous pending batch. If a checksum differs, restore the released file byte-for-byte and add a new forward migration. If baseline verification fails, compare the exact expected schema on a database backup or disposable clone. Do not use `db-rollback.sh` on released data; the script is a documented refusal path.

## Sandbox cleanup

Normal cleanup is automatic. When a process is killed, remove only project-owned resources:

```bash
docker ps -a --filter label=enten.sandbox=true --format '{{.ID}} {{.Status}}'
docker rm -f <exact-owned-container-id>
find /tmp -maxdepth 1 -type d -name 'enten-public-pr-*' -print
```

For the local stack:

```bash
scripts/dev-down.sh              # preserve database/model volumes
scripts/dev-down.sh --drop-data  # destructive, disposable environments only
```

Do not remove unrelated Testcontainers sessions, k3d containers, images, caches, or volumes by pattern alone.

Compose grants the worker a 15-minute shutdown grace period so a normal bounded claim can drain. A supervisor that uses a shorter timeout may terminate the worker; the expired lease is then charged to the retry budget and reclaimed by the next worker.

## Trace discontinuity

A job with a missing, malformed, or mismatched authenticated carrier fails before pipeline execution. Compare the stored `jobs.trace_id`, payload `traceparent`, edge enqueue span, and worker consume span. Public inbound trace headers should never be the root of the trusted trace.

Jaeger is available at `http://127.0.0.1:16686` in Compose. A complete review has a `POST /webhook` server span containing authentication, a `jobs enqueue` producer span, worker consume, and reserve/propose/findings/verify/persist/report pipeline spans.

## Cost and model components

The default production worker generator makes no LLM calls. `enten cost` reports persisted local `ReviewRun` totals; the LLM client additionally writes `llm_calls` when invoked by explicit model/eval callers. Provider outage procedures do not apply to the default worker.

## Error-budget check

`scripts/error-budget-check.sh` measures jobs that entered the real terminal states `done` or `failed` during the last 24 hours, using `updated_at`. A database/query error, malformed result, or a window with no terminal jobs is an unknown health state and exits nonzero; it is never reported as a passing budget. Set `ERROR_BUDGET_THRESHOLD` to a decimal from `0` through `1` when the default `0.99` is not appropriate.
