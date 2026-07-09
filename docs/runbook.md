# enten Runbook

Operational playbooks for the most common failure modes. Each scenario
lists symptoms, diagnostic commands, and remediation steps.

---

## 1. Bot stopped responding

**Symptoms:** No PR comments or check runs; webhook deliveries queue up;
worker process CPU/memory looks idle.

**Diagnose:**

1. Check Jaeger traces for the last hour:
   ```bash
   open http://localhost:16686
   # Service = "enten-worker"; look for errors or missing spans.
   ```
2. Check the queue state:
   ```bash
   docker compose exec -T postgres psql -U enten -d enten -c \
     "SELECT state, count(*) FROM jobs WHERE created_at > now() - interval '1 hour' GROUP BY state"
   ```
3. Check worker logs:
   ```bash
   docker compose logs -f --tail 200 worker
   ```

**Remediate:**

- If jobs are stuck in `indexing`/`reviewing`/`testing`/`verifying`/`commenting` for
  longer than expected, restart the worker container:
  ```bash
  docker compose restart worker
  ```
- If Postgres is unreachable, verify `database_url` / `DATABASE_URL` and that
  the `postgres` service is healthy (`docker compose ps`).
- If traces show repeated provider errors, see scenario 2.

---

## 2. Spike in failed jobs

**Symptoms:** Error budget check fails; `failed`/`errored` job counts jump;
GitHub Checks show "failure".

**Diagnose:**

1. Look at recent failure reasons:
   ```bash
   docker compose exec -T postgres psql -U enten -d enten -c \
     "SELECT state, error_message, count(*) FROM jobs WHERE state IN ('failed','errored') AND created_at > now() - interval '6 hours' GROUP BY state, error_message"
   ```
2. Check static-analysis rules for false positives:
   ```bash
   uv run semgrep --config rules/ --error tests/fixtures/prs/smoke/
   ```
3. Check LLM provider status:
   ```bash
   curl -s https://status.openai.com/api/v2/status.json | jq '.status.description'
   # or OpenRouter status page
   ```

**Remediate:**

- If a new Semgrep/Gitleaks rule is firing on benign fixtures, pin or disable
  the rule in `rules/` and restart the worker.
- If the provider is down, switch to the fallback provider via
  `LLM_PROVIDER=ollama` or an alternative OpenRouter model.
- Requeue affected PRs by inserting new jobs or waiting for the webhook replay.

---

## 3. Cost spike

**Symptoms:** `enten cost` shows unusually high spend; LLM provider bill
increases; many calls to an expensive model.

**Diagnose:**

1. Grep `llm_calls` for the high-cost provider/model:
   ```bash
   docker compose exec -T postgres psql -U enten -d enten -c \
     "SELECT model, count(*), sum(cost_usd) FROM llm_calls WHERE created_at > now() - interval '24 hours' GROUP BY model ORDER BY sum DESC"
   ```
2. Run the cost CLI for the last day:
   ```bash
   uv run enten cost --since 1d
   ```
3. Compare against the per-PR cap:
   ```bash
   docker compose exec -T postgres psql -U enten -d enten -c \
     "SELECT run_id, cost_usd FROM pr_reviews WHERE cost_usd > 0.50 AND created_at > now() - interval '24 hours'"
   ```

**Remediate:**

- Lower `PER_PR_COST_CAP_USD` and `LLM_MAX_ITERATIONS`.
- Switch the default model to a cheaper option (`gpt-4o-mini`, local Ollama).
- If a specific PR is looping, block it manually or add a circuit-breaker
  override.

---

## 4. Sandbox exhaustion

**Symptoms:** Jobs fail with sandbox creation errors; high Docker CPU/memory;
disk pressure on the worker host.

**Diagnose:**

1. List orphaned containers:
   ```bash
   docker ps -a --filter "label=enten.sandbox" --format "table {{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}"
   ```
2. Count sandbox rows without a matching destroyed container:
   ```bash
   docker compose exec -T postgres psql -U enten -d enten -c \
     "SELECT provider, count(*) FROM sandbox_runs WHERE destroyed_at IS NULL AND created_at < now() - interval '1 hour' GROUP BY provider"
   ```

**Remediate:**

- Run the sandbox cleanup script:
  ```bash
  docker compose exec worker uv run python -m enten.sandbox.cleanup
  # fallback: prune stopped containers manually
  docker container prune -f --filter "label=enten.sandbox"
  ```
- Restart the worker so the factory cache is cleared.
- If using E2B, verify `E2B_API_KEY` and check the E2B dashboard for stuck
  sandboxes.

---

## 5. Webhook 401

**Symptoms:** GitHub webhook deliveries fail with HTTP 401; signature
verification errors in TS edge logs.

**Diagnose:**

1. Check the configured secret:
   ```bash
   grep GITHUB_WEBHOOK_SECRET .env
   ```
2. Compare it with the secret shown in the GitHub App's **Webhook** section.
3. Verify the TS edge is reading the same secret:
   ```bash
   docker compose logs -f app | grep -i signature
   ```

**Remediate:**

- If the values differ, update `.env` (or the production secret store) to match
  the GitHub App config and restart the app service:
  ```bash
  docker compose restart app
  ```
- If the secret was rotated, re-deliver a recent webhook from the GitHub App
  **Advanced** tab to confirm the new signature verifies.

---

## See also

- `docs/oncall.md` — on-call rotation and escalation contacts
- `scripts/healthcheck-all.sh` — automated acceptance-criteria checks
- `scripts/error-budget-check.sh` — daily error-budget gate
