# Verifier demonstration contracts

The release has two deliberately separate demonstrations: an offline HTTPie image contract and a credentialed GitHub smoke test with the generic immutable Python verifier. They do not share a verifier image.

## Offline HTTPie image contract

`docker/verifier-httpie/` is pinned to the public `httpie/cli` commit `2105caa49bae87c5809c274e407619a0de2639d1`, Git tree `5be8d10a92efc928460e86f1ce6dcabbea1a728a`, and `tests/test_parser_schema.py` selector. The image also validates the normalized source digest transported by the worker.

The fixture proves the image and three-run verifier contract. It does not publish an image, create or verify a fork, install a GitHub App, or post to live GitHub. Any fork commit changes the trusted source identity and is rejected by design.

The opt-in integration test builds a unique local tag, runs nine candidate checks (three complete three-run verifier cycles), verifies metadata and negative contract mutations, and removes the tag and containers:

```bash
ENTEN_RUN_DEMO_IMAGE_INTEGRATION=1 \
  ENTEN_HTTPIE_SOURCE=/path/to/trusted/httpie-checkout \
  uv run --project packages/python/enten pytest tests/test_demo_image.py -q
```

The trusted checkout must match the recorded commit and tree exactly. The runtime container is networkless and read-only.

## Credentialed GitHub smoke test

Use a disposable public Python repository that you control. A fork of `Meillaya/enten` is suitable when the account running the demo does not already own that repository. Never install a test App on an upstream repository you do not control.

1. Copy `.env.example` to `.env` and configure the GitHub App ID, webhook secret, and PEM path.
2. Keep the generic immutable verifier configuration:

   ```dotenv
   SANDBOX_DOCKER_IMAGE=python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28
   VERIFICATION_COMMAND=python -m compileall -q .
   ```

3. Configure the externally reachable webhook origin, start `scripts/dev-up.sh`, install the App only on the public test repository, and open a non-draft pull request.
4. Confirm one job, one causal trace, one Check Run, and one bot-marker PR comment for the exact PR head SHA.

The generic verifier checks Python syntax only. It exercises the credentialed fetch, sandbox, and reporting path without claiming project-test coverage. Do not substitute the HTTPie image: its offline source contract cannot verify a fork or arbitrary PR head.

Live GitHub behavior is credential-dependent and is not claimed by the repository-only release gate.

## Fixture corpus

`tests/fixtures/prs/` contains independent `expected_findings.json` and `stub_findings.json` inputs for bugs, missing tests, security issues, prompt injection, and clean changes. Expected labels are scoring data only. Missing stub predictions, zero determinism tests, or malformed aggregate metrics fail the eval.
