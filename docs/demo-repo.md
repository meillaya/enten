# Demo repository

> Status: 🚧 stub — fleshed out by T17 (eval harness) and T18 (synthetic
> repos).

The "demo repository" is a tiny, deterministic Git repository that the
agent will be pointed at to exercise every end-to-end capability. It
exists so that:

1. CI can run reproducible smoke tests without network access.
2. The eval harness can score completions against a known-good diff.
3. Humans can poke the agent in a sandboxed way during development.

## Sketch

```
demo-repo/
├── README.md            # human-readable scenario description
├── .github/workflows/   # workflow runs the agent will be asked to read
├── src/                 # intentionally small Python module
│   └── __init__.py
└── tests/
    └── test_smoke.py
```

## Tasks the demo repo must cover

| Task kind             | Scenario                          | Validated by |
|-----------------------|-----------------------------------|--------------|
| Read + summarize      | Inspect repo, return a one-liner  | T17 (eval)   |
| Patch a bug           | Fix a failing test                | T18          |
| Open a PR             | Push a branch, file a PR          | T25          |
| Review a PR           | Comment on a draft PR             | T26          |

## How it is consumed

* **Local dev**: `make dev` brings up the demo repo on `localhost:4000`
  and the enten edge on `localhost:3000`.
* **CI**: T07 will define a job that resets the demo repo to a known
  commit, runs the agent, then diffs the result.

See also `architecture.md` for the broader system shape.
