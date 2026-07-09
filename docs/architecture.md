# Architecture

> Status: 🚧 under construction — populated alongside T02 (devenv+compose),
> T04 (runtime bootstrap), and T08 (database).

## High-level shape

```
enten/
├── packages/
│   ├── python/enten/   # Python core: agent loop, planners, tool registry
│   └── ts/app/         # TypeScript app: webhook intake, HTTP API, OTel wiring
├── docs/               # Architecture, ADRs, demo-repo notes
├── prompts/            # T28: versioned prompt library (placeholder)
├── rules/              # T19: rule packs loaded by the agent (placeholder)
├── Makefile            # cross-language install/test/lint/format/build
├── package.json        # pnpm workspace root
├── pnpm-workspace.yaml
└── pyproject.toml      # uv workspace root
```

## Language split (rationale)

* **Python** owns the agent's reasoning loop and the tool registry, because
  the existing ecosystem (Typer, pydantic, langchain-style planners) is
  Python-first and many inference SDKs are most stable there.
* **TypeScript** owns the network edge — GitHub webhook intake, HTTP API,
  structured logging, and the OpenTelemetry SDK — because Hono is the
  lightest viable HTTP framework for a Node-22 footprint and the GitHub
  webhook SDK is canonical in TypeScript.

Both halves run in the same process tree for development (`make dev` boots
the TS edge and the Python loop together) and are deployed as separate
containers in production (see T02 docker-compose).

## Cross-cutting

* **Configuration** flows from `.env` → `pydantic-settings` (Python) and
  `zod`-validated env objects (TypeScript). T05 owns the schema.
* **Observability** uses OpenTelemetry SDK in TS at the edge, with a
  Python OTLP exporter shipped in T08 to share one trace tree.
* **Tests** use `pytest` (Python) and `vitest` (TypeScript). T06 wires
  golden tests and contract tests that cross the language boundary.

## See also

* `decisions/0001-monorepo.md` — why a single repo, not two.
* `decisions/0002-hybrid-language.md` — why Python *and* TypeScript.
* `demo-repo.md` — the end-to-end scenario every change is validated
  against.
