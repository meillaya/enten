# Demo recording script

This sequence runs the real edge and worker against a disposable public Python repository. It requires a GitHub App, an externally reachable HTTPS origin that forwards `/webhook` to `127.0.0.1:3000`, and a readable App private key.

## 1. Clone and install

```bash
git clone https://github.com/Meillaya/enten.git
cd enten
make install
```

## 2. Prepare the generic verifier and GitHub App

```bash
cp .env.example .env
docker pull python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28
```

Replace the example hook origin in `packages/ts/app/github-app.yml` with the public HTTPS origin, create the App from that manifest, and put its real App ID, webhook secret, and PEM path in `.env`. Keep the generic immutable verifier settings:

```dotenv
SANDBOX_DOCKER_IMAGE=python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28
VERIFICATION_COMMAND=python -m compileall -q .
```

The worker rejects mutable image tags before Docker client creation. This command is intentionally only a Python syntax smoke check.

## 3. Start and probe the complete stack

```bash
scripts/dev-up.sh
scripts/healthcheck-all.sh
```

Narration: "The authenticated TypeScript edge is listening on port 3000 and the Python worker is polling the migrated Postgres queue."

## 4. Create or fork a public Python test repository

When the demo account does not own `Meillaya/enten`, it can create a disposable fork:

```bash
gh repo fork Meillaya/enten --clone=false
```

Otherwise, create a separate public test repository with Python source. Install the App only on the repository you control, then open a non-draft pull request. Do not use the pinned HTTPie verifier image for this path: its offline contract accepts only one exact upstream commit and rejects fork heads.

## 5. Watch the real processes

```bash
docker compose logs --follow web worker
```

Wait for the authenticated enqueue and the worker's claim, exact-SHA checkout, syntax verification, reporting, and completion records.

## 6. Show the result

Refresh the pull request. The App produces one Check Run and one marker-bearing comment for the reviewed head SHA.

## 7. Run the independent eval smoke

```bash
uv run --project packages/python/enten enten eval \
  --corpus tests/fixtures/prs/smoke \
  --report /tmp/enten-demo-eval.json \
  --stub-llm
cat /tmp/enten-demo-eval.json
```

## 8. Show traces

```bash
open http://localhost:16686
```

Narration: "The edge and worker share one causal trace. The default worker makes no implicit model calls."

## 9. Stop the demo

```bash
scripts/dev-down.sh
rm -f /tmp/enten-demo-eval.json
```

The offline HTTPie image test is a separate release fixture documented in `docs/demo-repo.md`; it is not part of this credentialed recording path.
