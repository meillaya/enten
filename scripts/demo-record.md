# Demo recording script

Screen-record-ready command sequence for enten.

## 1. Clone and install

```bash
git clone https://github.com/<owner>/enten.git
cd enten
make install
```

## 2. Start local stack

```bash
docker compose up -d postgres jaeger
./scripts/db-migrate.sh
```

## 3. Start agent

```bash
make dev
```

Narration: "The TypeScript edge is listening on port 3000 and the Python worker is polling Postgres for jobs."

## 4. Fork target repo

```bash
gh repo fork httpie/cli --clone=false --remote=true
```

## 5. Open a PR

In the browser, create or open a pull request in your `httpie/cli` fork.

## 6. Watch the worker

In the terminal running `make dev`, wait for log lines:

```
job claimed -> indexing -> reviewing -> testing -> verifying -> commenting -> done
```

## 7. Show the result

Refresh the PR in the browser. The enten-bot comment appears with findings, verification status, and cost.

## 8. Run eval smoke

In a second terminal:

```bash
uv run enten eval --corpus tests/fixtures/prs/smoke --stub-llm
```

## 9. Show traces

```bash
open http://localhost:16686
```

Narration: "Every job leaves a trace in Jaeger and every LLM call is logged with cost."
