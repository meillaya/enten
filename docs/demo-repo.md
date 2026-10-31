# Verifier demonstration contracts

The release has two separate demonstrations: an offline HTTPie image contract
and a credentialed GitHub smoke test with the generic immutable Python
verifier. They use different verifier images.

## Offline HTTPie image contract

`docker/verifier-httpie/` is pinned to the public `httpie/cli` commit
`2105caa49bae87c5809c274e407619a0de2639d1`, Git tree
`5be8d10a92efc928460e86f1ce6dcabbea1a728a`, and the
`tests/test_parser_schema.py` selector. The image also validates the normalized
source digest that the worker transports.

The fixture proves the image and the three-run verifier contract. Publishing an
image, creating or verifying a fork, installing a GitHub App, and posting to
live GitHub sit outside it. A fork commit changes the trusted source identity,
so the image rejects it.

The opt-in integration test builds a unique local tag. It runs nine candidate
checks (three complete three-run verifier cycles), verifies metadata and
negative contract mutations, then removes the tag and containers:

```bash
ENTEN_RUN_DEMO_IMAGE_INTEGRATION=1 \
  ENTEN_HTTPIE_SOURCE=/path/to/trusted/httpie-checkout \
  uv run --project packages/python/enten pytest tests/test_demo_image.py -q
```

The trusted checkout must match the recorded commit and tree. The runtime
container has no network and a read-only root filesystem.

## Credentialed GitHub smoke test

Use a disposable public Python repository that you control. A fork of
`Meillaya/enten` works when the demo account does not own the upstream
repository. Install the test App only on a repository you control.

1. Copy `.env.example` to `.env` and set the GitHub App ID, webhook secret, and
   PEM path.
2. Keep the generic immutable verifier configuration:

   ```dotenv
   SANDBOX_DOCKER_IMAGE=python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28
   VERIFICATION_COMMAND=python -m compileall -q .
   ```

3. Configure the externally reachable webhook origin, start
   `scripts/dev-up.sh`, install the App on the public test repository, and open
   a non-draft pull request.
4. Confirm one job, one causal trace, one Check Run, and one bot-marker pull
   request comment for the exact head SHA.

The generic verifier checks Python syntax only. It exercises the credentialed
fetch, sandbox, and reporting path. It does not measure the repository's test
suite. The HTTPie image cannot verify a fork or an arbitrary pull request head,
so keep it out of this test.

Live GitHub behavior depends on credentials. The repository-only release gate
does not claim it.

## Fixture corpus

`tests/fixtures/prs/` contains independent `expected_findings.json` and
`stub_findings.json` inputs for bugs, missing tests, security issues, prompt
injection, and clean changes. Expected labels are scoring data only. Missing
stub predictions, zero determinism tests, and malformed aggregate metrics fail
the eval.
