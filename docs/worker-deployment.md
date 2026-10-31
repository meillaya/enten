# Deploy and test the worker

This guide brings up the verified worker on a Linux host with Docker. It ends
with a live pull request that produces one Check Run and one comment.

## Prerequisites

- A Linux host with Docker Engine 24 or newer and Compose v2.
- 4 GiB RAM, 2 CPUs, and 20 GiB free disk for images, builds, and sandboxes.
- A public HTTPS origin that routes `/webhook` to port 3000. Caddy or nginx on
  the host works, and GitHub must be able to reach it.
- A GitHub App created from `packages/ts/app/github-app.yml`, installed on a
  disposable public test repository. Download the PEM private key.
- An immutable verifier image digest and an argv command that runs the test
  suite. The image must already contain the test dependencies because the
  verifier container has no network.

## 1. Prepare the host

```bash
git clone https://github.com/Meillaya/enten.git
cd enten
cp .env.example .env
```

Put the GitHub App PEM at `/etc/enten/github-app.pem` and make it readable by
uid 1000. The worker mounts that path read-only and refuses to start when the
file is missing.

## 2. Create the GitHub App

In GitHub, create an App with the manifest as the reference:

- Homepage: your repository URL.
- Webhook URL: `https://<your-origin>/webhook`.
- Webhook secret: the same value as `GITHUB_WEBHOOK_SECRET`.
- Permissions: `metadata: read`, `pull_requests: write`, `checks: write`.
- Subscribe to `pull_request` only.

Install the App on the test repository and download its private key.

## 3. Configure the stack

Edit `.env`:

```dotenv
GITHUB_APP_ID=<numeric app id>
GITHUB_APP_SLUG=<app slug>
GITHUB_APP_PRIVATE_KEY_PATH=/etc/enten/github-app.pem
GITHUB_WEBHOOK_SECRET=<openssl rand -hex 32>

DATABASE_URL=postgresql+asyncpg://enten:enten@postgres:5432/enten
OTEL_ENDPOINT=http://jaeger:4317

SANDBOX_DOCKER_IMAGE=<registry>/<image>@sha256:<64 hex>
VERIFICATION_COMMAND=pytest -q
```

The edge and the worker must use the same `SANDBOX_DOCKER_IMAGE` and
`VERIFICATION_COMMAND`. The enqueued job binds both values into the review
contract, and the worker rejects a mismatch.

`VERIFICATION_COMMAND` is parsed as argv. It does not run through a shell, so
pipes, redirection, and `&&` do not work. Put those steps in a script inside the
verifier image and call the script.

## 4. Start the stack

```bash
DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
scripts/dev-up.sh
scripts/healthcheck-all.sh
```

`dev-up.sh` starts Postgres, Jaeger, the migration role, the edge, and the
worker. It fails before starting when the Docker socket, App ID, webhook
secret, or PEM file is missing.

## 5. Verify the services

```bash
curl --fail http://127.0.0.1:3000/health
curl --fail http://127.0.0.1:3000/ready
curl --fail http://127.0.0.1:3000/metrics
```

Jaeger runs at `http://127.0.0.1:16686`.

## 6. Prove the live path

Open a non-draft pull request in the test repository. The App must receive one
`pull_request` delivery. Confirm:

- one job reaches state `done`;
- one Check Run has the verifier conclusion;
- one bot comment carries the head-SHA marker;
- the trace has `POST /webhook`, `jobs enqueue`, worker consume, and pipeline
  spans;
- `external_effects` has a `succeeded` row for the check and the comment.

```bash
docker compose exec -T postgres psql -U enten -d enten -c \
  "SELECT run_id,state,attempts,last_error FROM jobs ORDER BY created_at DESC LIMIT 5"

docker compose exec -T postgres psql -U enten -d enten -c \
  "SELECT effect_key,state,external_id FROM external_effects ORDER BY prepared_at DESC LIMIT 5"
```

This is the full release path: authenticated ingress, durable queue, exact
checkout, Docker verification, deterministic verdict, and GitHub reporting.

## Keep the web tier on Railway

The worker can run on a separate Docker host while the edge and migrations stay
on Railway. Both processes must use the same Postgres database and the same
verifier contract.

1. On the worker host, link the Railway project and open an SSH tunnel to the
   database:

   ```bash
   railway link
   railway connect Postgres --tunnel-only
   ```

   Keep that command running under systemd. It prints a local port.

2. Build the production image on the worker host:

   ```bash
   docker build -t enten:release .
   ```

3. Run the worker with host networking so it can reach the tunnel:

   ```bash
   docker run -d --name enten-worker --restart unless-stopped \
     --network host \
     --group-add "$(stat -c '%g' /var/run/docker.sock)" \
     -v /var/run/docker.sock:/var/run/docker.sock \
     -v /etc/enten/github-app.pem:/run/secrets/github-app.pem:ro \
     -e DATABASE_URL='postgresql://postgres:<password>@127.0.0.1:<tunnel-port>/railway' \
     -e GITHUB_APP_ID='<numeric app id>' \
     -e GITHUB_APP_PRIVATE_KEY_PATH=/run/secrets/github-app.pem \
     -e SANDBOX_DOCKER_IMAGE='<registry>/<image>@sha256:<64 hex>' \
     -e VERIFICATION_COMMAND='pytest -q' \
     -e OTEL_ENDPOINT='http://127.0.0.1:4317' \
     --entrypoint enten enten:release worker
   ```

4. Point the GitHub webhook at the Railway domain. Set the same
   `SANDBOX_DOCKER_IMAGE` and `VERIFICATION_COMMAND` on the Railway edge.
5. Repeat the pull request check from step 6.

This mixed topology is not covered by `scripts/dev-up.sh` or the release test
matrix. The SSH tunnel is a single point of failure, and the worker host needs
a restart policy for both the tunnel and the worker. Test it with one pull
request before you rely on it.

## Why Railway cannot host the worker

Railway runs unprivileged containers and provides no Docker socket. The worker
needs that socket to create the verifier container as a sibling. A Railway
worker service would start and then fail at the first verification call.

E2B is not a fallback. The adapter fails closed until an approved SDK
implementation and equivalent lifecycle tests exist.

A remote Docker daemon is technically possible through `DOCKER_HOST` because
the Docker SDK reads it, but the release does not test that path. Exposing the
Docker API over TCP requires mutual TLS and a dedicated host. Treat it as an
unsupported experiment, not a deployment option.
