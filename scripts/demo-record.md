# Demo recording script

This sequence runs the real edge and worker against a disposable public fork. It requires a GitHub App, an externally reachable HTTPS origin that forwards `/webhook` to `127.0.0.1:3000`, and a readable App private key.

## 1. Clone and install

```bash
git clone https://github.com/Meillaya/enten.git
cd enten
make install
```

## 2. Prepare the verifier and GitHub App

```bash
cp .env.example .env
docker build -t enten-verifier-httpie:3.2.4 docker/verifier-httpie
```

Replace the example hook origin in `packages/ts/app/github-app.yml` with the public HTTPS origin, create the App from that manifest, and put its real App ID, webhook secret, PEM path, verifier image, and verifier command in `.env`:

```dotenv
SANDBOX_DOCKER_IMAGE=enten-verifier-httpie:3.2.4
VERIFICATION_COMMAND=python /opt/verifier/verify.py
```

## 3. Start and probe the complete stack

```bash
scripts/dev-up.sh
scripts/healthcheck-all.sh
```

Narration: "The authenticated TypeScript edge is listening on port 3000 and the Python worker is polling the migrated Postgres queue."

## 4. Fork the target and open a pull request

```bash
gh repo fork httpie/cli --clone=false
```

Install the App on your public fork, then create or open a non-draft pull request there. Never install the demo App on the upstream repository.

## 5. Watch the real processes

```bash
docker compose logs --follow web worker
```

Wait for the authenticated enqueue and the worker's claim, verification, reporting, and completion records.

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
