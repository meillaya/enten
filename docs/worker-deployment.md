# Deploy and test the worker

This guide deploys the verified worker on a Linux host with Docker and proves
the full release path with a live pull request. The worker needs a Docker
daemon, the GitHub App private key, and a Postgres database.

The test is complete when one pull request produces one Check Run, one bot
comment, one `done` job, a complete trace, and two `succeeded` effect rows.

## Prerequisites

- A Linux host with Docker Engine 24 or newer and Compose v2.
- 4 GiB RAM, 2 CPUs, and 20 GiB free disk for images, builds, and sandboxes.
- A public HTTPS origin that routes `/webhook` to port 3000. Caddy or nginx on
  the host works, and GitHub must be able to reach it.
- A GitHub App installed on a disposable public test repository, with its PEM
  private key available on the host.
- An immutable verifier image and an argv command that runs the test suite. The
  image must already contain the test dependencies because the verifier
  container has no network.

## 1. Prepare the host

Install Docker Engine and the Compose plugin from the Docker repository for
your distribution. Add the deployment user to the `docker` group or run the
commands with sudo. Then create the secret directory and clone the repository:

```bash
sudo install -d -m 0750 /etc/enten
git clone https://github.com/Meillaya/enten.git
cd enten
cp .env.example .env
```

Put the GitHub App PEM at `/etc/enten/github-app.pem` and make it readable by
uid 1000. The worker mounts that path read-only and refuses to start when the
file is missing.

## 2. Build the verifier image

The verifier image is the test environment. It must run the repository's real
suite without network access, so install every dependency at build time. A
minimal Python image looks like this:

```dockerfile
FROM python:3.12-slim@sha256:<python-digest>
RUN pip install --no-cache-dir pytest
```

Build and push it, then record the registry digest:

```bash
docker build -t registry.example.com/enten-verifier:1 .
docker push registry.example.com/enten-verifier:1
docker image inspect registry.example.com/enten-verifier:1 \
  --format '{{index .RepoDigests 0}}'
```

Set `SANDBOX_DOCKER_IMAGE` to that `name@sha256:<64 hex>` reference. A local
image ID in `sha256:<64 hex>` form also works when the worker host already has
the image, but a registry digest survives host rebuilds.

The verifier command runs as uid 1000 inside the image. The image must expose
the test runner on `PATH` and must not depend on network access. If the suite
needs a database, a browser, or a compiler, install those pieces in the image
before the test.

## 3. Expose the webhook over HTTPS

GitHub requires HTTPS. Put a reverse proxy in front of port 3000 and keep the
application port off the public interface. A Caddy configuration on the host:

```caddyfile
enten.example.com {
  reverse_proxy 127.0.0.1:3000
}
```

Caddy obtains and renews the certificate. Open ports 80 and 443 in the host
firewall. The endpoint returns 502 until the stack starts in step 6, so confirm
the certificate is issued before you create the App:

```bash
curl -sI https://enten.example.com/health
```

## 4. Create the GitHub App

Generate the webhook secret first:

```bash
openssl rand -hex 32
```

Use `packages/ts/app/github-app.yml` as the reference for the App fields. In
GitHub, create the App with:

- Homepage: the repository URL.
- Webhook URL: `https://<your-origin>/webhook`.
- Webhook secret: the value from the command above.
- Permissions: `metadata: read`, `pull_requests: write`, `checks: write`.
- Subscribe to `pull_request` only.

Install the App on the test repository and download its private key. The App
must be installed on the repository that will receive the test pull request.
The App does not need `contents`, `actions`, or administration permission.

## 5. Configure the stack

Edit `.env`:

```dotenv
GITHUB_APP_ID=<numeric app id>
GITHUB_APP_SLUG=<app slug>
GITHUB_APP_PRIVATE_KEY_PATH=/etc/enten/github-app.pem
GITHUB_WEBHOOK_SECRET=<the generated secret>

DATABASE_URL=postgresql+asyncpg://enten:enten@postgres:5432/enten
OTEL_ENDPOINT=http://jaeger:4317

REVIEW_GENERATOR=llm
LLM_PROVIDER=openai-compat
LLM_OPENAI_BASE_URL=https://openrouter.ai/api/v1
LLM_OPENAI_API_KEY=<provider key>
LLM_DEFAULT_MODEL=deepseek/deepseek-v4.1-flash-1017
PER_PR_COST_CAP_USD=0.50

SANDBOX_DOCKER_IMAGE=<registry>/<image>@sha256:<64 hex>
VERIFICATION_COMMAND=pytest -q
```

`REVIEW_GENERATOR=llm` applies to the worker. Use `openai-compat` for
OpenRouter and LM Studio, `anthropic` for the native Messages API, or `ollama`
for a local daemon. The edge and the worker must use the same
`SANDBOX_DOCKER_IMAGE` and `VERIFICATION_COMMAND`. The enqueued job binds both values into the review
contract, and the worker rejects a mismatch.

`VERIFICATION_COMMAND` is parsed as argv. It does not run through a shell, so
pipes, redirection, and `&&` do not work. Put those steps in a script inside the
verifier image and call the script.

## 6. Start the stack

```bash
DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)
scripts/dev-up.sh
scripts/healthcheck-all.sh
```

`dev-up.sh` starts Postgres, Jaeger, the migration role, the edge, and the
worker. It fails before starting when the Docker socket, App ID, webhook
secret, or PEM file is missing.

## 7. Verify the services

```bash
curl --fail http://127.0.0.1:3000/health
curl --fail http://127.0.0.1:3000/ready
curl --fail http://127.0.0.1:3000/metrics
```

Jaeger runs at `http://127.0.0.1:16686`. The worker log must show one claim and
one completion for each job. The edge log must show one persisted job for each
delivery.

## 8. Prove the live path

Open a non-draft pull request in the test repository. The App must receive one
`pull_request` delivery. Confirm:

- one job reaches state `done`;
- one Check Run has the verifier conclusion;
- one bot comment carries the marker derived from the head SHA;
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

If the job stays queued, check the worker log for a Docker or PEM error. If the
job reaches `done` and GitHub has no check, query `external_effects` for an
`indeterminate` row and follow the runbook.

## Troubleshooting container egress

The worker and the verifier need outbound access to GitHub. Test the container
network when the host can reach GitHub but the worker cannot:

```bash
docker run --rm alpine wget -qO- --timeout=5 https://api.github.com
```

If that times out, Docker is not forwarding return traffic. On a host where
`dockerd` was started manually, or where the host firewall sets the `FORWARD`
policy to `DROP`, add the established-traffic rule:

```bash
sudo iptables -I DOCKER-FORWARD 1 \
  -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
```

Apply the rule in the host firewall service so it survives a reboot. A normal
Docker Engine install adds this rule itself.

## 9. Clean up the test

```bash
scripts/dev-down.sh
```

This preserves the Postgres volume. Use `--drop-data` only for a disposable
host. Suspend the App installation and revoke the private key when the test is
over. Remove the DNS record and the host if you no longer need them.

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
5. Repeat the pull request check from step 8.

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
