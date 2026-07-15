# HTTPie verifier demonstration

The release includes a hermetic verifier image for a pinned public `httpie/cli` snapshot. It proves the image and three-run verifier contract; it does not publish an image, create a fork, install a GitHub App, or post to live GitHub during CI.

## Immutable contract

`docker/verifier-httpie/` records:

- upstream repository and commit;
- expected Git tree;
- one trusted offline pytest selector;
- hash-locked build and runtime dependencies;
- non-root runtime and minimal image contents.

The integration test builds a unique local tag, runs nine candidate checks (three complete three-run verifier cycles), verifies metadata and negative contract mutations, and removes the tag and containers.

```bash
ENTEN_RUN_DEMO_IMAGE_INTEGRATION=1 \
  ENTEN_HTTPIE_SOURCE=/path/to/trusted/httpie-checkout \
  uv run pytest tests/test_demo_image.py -q
```

The trusted checkout must already match the recorded commit/tree. The runtime container is networkless and read-only.

## Local GitHub demonstration

1. Fork `httpie/cli` into an account you control; never install the test App on upstream.
2. Build the image:

   ```bash
   docker build -t enten-verifier-httpie:3.2.4 docker/verifier-httpie
   ```

3. Configure `.env`:

   ```dotenv
   SANDBOX_DOCKER_IMAGE=enten-verifier-httpie:3.2.4
   VERIFICATION_COMMAND=python /opt/verifier/verify.py
   ```

4. Configure the GitHub App credentials described in the README, start `scripts/dev-up.sh`, install the App on the public fork, and open a non-draft PR.
5. Confirm one job, one causal trace, one Check Run, and one bot-marker PR comment for the PR head SHA.

Live GitHub behavior is credential-dependent and is not claimed by the repository-only release gate.

## Fixture corpus

`tests/fixtures/prs/` contains independent `expected_findings.json` and `stub_findings.json` inputs for bugs, missing tests, security issues, prompt injection, and clean changes. Expected labels are scoring data only. Missing stub predictions, zero determinism tests, or malformed aggregate metrics fail the eval.
