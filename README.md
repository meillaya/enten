# enten

[![CI](https://github.com/<owner>/enten/actions/workflows/ci.yml/badge.svg)](https://github.com/<owner>/enten/actions/workflows/ci.yml)
[![Eval Smoke](https://github.com/<owner>/enten/actions/workflows/ci.yml/badge.svg?job=python-eval-smoke)](https://github.com/<owner>/enten/actions/workflows/ci.yml)

Status: 🚧 under construction

enten is an AI engineering agent that lives on top of your Git
repositories: it ingests issues and pull requests, plans changes, runs
tools, and ships patches back as PRs. This monorepo hosts both halves of
the system — a Python reasoning core and a TypeScript webhook/HTTP edge
— alongside a small demo repository used for end-to-end validation.

For the high-level design, see [`docs/architecture.md`](docs/architecture.md).
For the rationale behind the monorepo and language split, see the
ADRs in [`docs/decisions/`](docs/decisions/).

## Quick start

```bash
make install   # uv sync + pnpm install
make test      # pytest + vitest
make dev       # ts watch mode (Python loop is run separately)
```

`make help` lists every available target.
