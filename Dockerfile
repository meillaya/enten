# syntax=docker/dockerfile:1

FROM node:22.13.0-bookworm-slim@sha256:f5a0871ab03b035c58bdb3007c3d177b001c2145c18e81817b71624dcf7d8bff AS node
FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10.4@sha256:4cac394b6b72846f8a85a7a0e577c6d61d4e17fe2ccee65d9451a8b3c9efb4ac /uv /uvx /bin/
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules

RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install --global pnpm@11.10.0

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY packages/python ./packages/python
RUN uv sync --frozen --all-packages --no-dev

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/ts ./packages/ts
RUN export CI=true \
    && pnpm install --frozen-lockfile \
    && pnpm --filter './packages/ts/*' build \
    && pnpm prune --prod

FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b AS runtime

LABEL org.opencontainers.image.title="enten"
LABEL org.opencontainers.image.description="Deterministic GitHub pull-request review agent"
LABEL org.opencontainers.image.source="https://github.com/Meillaya/enten"

RUN groupadd --gid 1000 enten \
    && useradd --uid 1000 --gid enten --shell /usr/sbin/nologin --create-home enten

WORKDIR /app

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=builder --chown=enten:enten /app/.venv ./.venv
COPY --from=builder --chown=enten:enten /app/packages/ts/app/dist ./packages/ts/app/dist
COPY --from=builder --chown=enten:enten /app/packages/ts/app/node_modules ./packages/ts/app/node_modules
COPY --chown=enten:enten packages/python ./packages/python
COPY --chown=enten:enten migrations ./migrations
COPY --chown=enten:enten prompts ./prompts
COPY --chown=enten:enten rules ./rules
COPY --chown=enten:enten health.py ./health.py

ENV DATABASE_URL=postgresql+asyncpg://enten:enten@postgres:5432/enten \
    ENTEN_MIGRATIONS_DIR=/app/migrations \
    HOME=/home/enten \
    METRICS_PORT=9091 \
    NODE_ENV=production \
    PATH=/app/.venv/bin:$PATH \
    PORT=3000 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER 1000:1000

EXPOSE 3000 9091

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["python", "/app/health.py"]

CMD ["node", "packages/ts/app/dist/server.js"]
