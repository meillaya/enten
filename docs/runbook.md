# Operations runbook

These commands assume the repository root and the Compose project name `enten`.
Clean up by Compose project, sandbox label, exact container ID, or test-session
label. Global Docker and volume prune commands are unsafe on shared hosts.

## Webhook accepts but enqueues no job

1. Check edge health and logs:

   ```bash
   curl --fail http://127.0.0.1:3000/health
   docker compose logs --tail 200 web
   ```

2. Query recent queue state:

   ```bash
   docker compose exec -T postgres psql -U enten -d enten -c \
     "SELECT state, count(*) FROM jobs WHERE created_at > now() - interval '1 hour' GROUP BY state ORDER BY state"
   ```

3. Check the GitHub delivery response:
   - `401`: webhook secret or signature mismatch;
   - `413`: body exceeds the configured limit;
   - `415`: content type is not `application/json`;
   - `422`: malformed supported payload;
   - `503`: enqueue deadline or database failure;
   - `200 ignored`: unsupported event, action, or draft pull request.

Rotate a compromised webhook secret in GitHub and the deployment secret store.
Restart `web`, then redeliver a non-sensitive recent event.

## Worker is not claiming

```bash
docker compose logs --tail 200 worker
docker compose ps
docker compose exec -T postgres psql -U enten -d enten -c \
  "SELECT run_id,state,attempts,claimed_by,claim_generation,lease_expires_at,last_error FROM jobs ORDER BY created_at DESC LIMIT 20"
```

The worker refuses to start without an App ID and a readable PEM. It also needs
the Docker socket and the correct supplemental group. Check both:

```bash
stat -c '%g %a %U:%G' /var/run/docker.sock
docker compose config --format json | jq '.services.worker | {group_add,volumes,environment}'
```

Restarting the worker is safe. Expired leases are reclaimed with a new
generation, and stale generations cannot update checkpoints.

## Verification failures

A nonzero candidate exit is a product verdict. An `error` points to checkout,
sandbox, parser, scanner, or configuration failure.

```bash
docker compose logs --tail 300 worker
docker ps -a --filter label=enten.sandbox=true \
  --format 'table {{.ID}}\t{{.Image}}\t{{.Status}}'
```

Confirm that `SANDBOX_DOCKER_IMAGE` exists on the worker host and that
`VERIFICATION_COMMAND` is valid for that image. The default compileall command
is a syntax smoke check. E2B is unsupported; do not select it as an emergency
fallback.

## Indeterminate GitHub effect

An effect becomes `indeterminate` when a GitHub request may have succeeded but
its receipt is not durable. Automatic retry is disabled to prevent duplicates.

1. Stop the worker.
2. Query the effect and matching checkpoints:

   ```bash
   docker compose exec -T postgres psql -U enten -d enten -c \
    "SELECT run_id,effect_key,state,marker,external_id,last_error FROM external_effects WHERE state='indeterminate' ORDER BY COALESCE(completed_at,attempted_at,prepared_at) DESC"
   ```

3. Use the marker and reviewed SHA to inspect existing GitHub checks and
   comments.
4. If the check exists, adopt it with the operator command:

   ```bash
   docker compose run --rm --no-deps worker enten reconcile \
     --run-id <run_id> --check-id <check_run_id>
   ```

   The command fetches the check, verifies its head SHA and App ID, records the
   observed ID, and requeues the job. The worker posts the missing comment and
   finishes the run. Pass `--comment-id` as well when both writes already exist;
   that records both receipts without writing again.
5. Start the worker after the command requeues the job.

Never change `indeterminate` back to `prepared` without an observed external ID.
Migration `0004_effect_reconciliation.sql` allows only
`indeterminate -> succeeded` with a non-null external ID. The guard trigger
rejects every other undocumented transition.

## Migration drift or baseline failure

```bash
scripts/db-migrate.sh
```

The idempotent apply command validates every recorded filename and raw-byte
checksum before it applies a contiguous pending batch. If a checksum differs,
restore the released file byte for byte and add a new forward migration. If
baseline verification fails, compare the exact expected schema on a database
backup or a disposable clone. Do not run `db-rollback.sh` against released data.
The script is a documented refusal path.

## Sandbox cleanup

Normal cleanup is automatic. When a process is killed, remove only
project-owned resources:

```bash
docker ps -a --filter label=enten.sandbox=true --format '{{.ID}} {{.Status}}'
docker rm -f <exact-owned-container-id>
find /tmp -maxdepth 1 -type d -name 'enten-public-pr-*' -print
```

For the local stack:

```bash
scripts/dev-down.sh              # preserve database and model volumes
scripts/dev-down.sh --drop-data  # destructive, disposable environments only
```

Remove unrelated Testcontainers sessions, k3d containers, images, caches, and
volumes by their own identifiers, not by a broad pattern.

Compose gives the worker a 15-minute shutdown grace period, so a normal bounded
claim can drain. A supervisor with a shorter timeout can terminate the worker.
The expired lease is then charged to the retry budget and reclaimed by the next
worker.

## Trace discontinuity

A job with a missing, malformed, or mismatched authenticated carrier fails
before the pipeline runs. Compare the stored `jobs.trace_id`, the payload
`traceparent`, the edge enqueue span, and the worker consume span. Public
inbound trace headers are never the root of the trusted trace.

Jaeger runs at `http://127.0.0.1:16686` in Compose. A complete review has a
`POST /webhook` server span, a `jobs enqueue` producer span, a worker consume
span, and reserve, propose, findings, verify, persist, and report spans.

## Cost and model components

The default production worker generator makes no LLM calls. `enten cost`
reports persisted local `ReviewRun` totals. The LLM client also writes
`llm_calls` rows when an explicit model or eval caller invokes it. Provider
outage procedures apply only to those callers.

## Error-budget check

`scripts/error-budget-check.sh` measures jobs that entered the terminal states
`done` or `failed` during the last 24 hours, using `updated_at`. A database or
query error, a malformed result, or a window with no terminal jobs is an
unknown health state and exits nonzero. The script never reports an unknown
state as a passing budget. Set `ERROR_BUDGET_THRESHOLD` to a decimal from `0`
through `1` when the default `0.99` does not fit.
