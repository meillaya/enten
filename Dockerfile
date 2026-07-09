# syntax=docker/dockerfile:1
# ─────────────────────────────────────────────────────────────────────────
# enten — production image
#
# Multi-stage build:
#   1. builder  : resolve Python deps with uv, build TypeScript apps with pnpm
#   2. runtime  : copy the venv + built artifacts, run as non-root
#
# Health check hits the TS metrics port (9090) which serves the same Hono
# app as the main webhook port (3000), so /health is available there too.
# ─────────────────────────────────────────────────────────────────────────

# ─── Builder stage ───────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b AS builder

# Pin uv to a released version — never use :latest in production.
COPY --from=ghcr.io/astral-sh/uv:0.5.5@sha256:dc60491f42c9c7228fe2463f551af49a619ebcc9cbd10a470ced7ada63aa25d4 \
    /uv /uvx /bin/

# Pin Node.js to the LTS version required by the TS workspace.
ARG NODE_VERSION=22.13.0
ARG PNPM_VERSION=11.10.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        xz-utils \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
        | tar -xJf - -C /usr/local --strip-components=1 \
    && npm install -g "pnpm@${PNPM_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy Python workspace manifests first so `uv sync` can cache the layer.
COPY pyproject.toml uv.lock ./
COPY README.md ./
COPY packages/python ./packages/python

# Copy TypeScript workspace manifests.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY packages/ts ./packages/ts

# Install Python dependencies for the whole workspace (without the projects
# themselves — source is copied in the runtime stage and installed there).
RUN uv sync --frozen --no-install-project --all-packages

# Install and build the TypeScript workspace.
RUN pnpm install --frozen-lockfile \
    && pnpm --filter './packages/ts/*' build

# ─── Runtime stage ───────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm@sha256:8a7e7cc04fd3e2bd787f7f24e22d5d119aa590d429b50c95dfe12b3abe52f48b AS runtime

LABEL org.opencontainers.image.title="enten"
LABEL org.opencontainers.image.description="enten - AI engineering agent"
LABEL org.opencontainers.image.source="https://github.com/enten/enten"

ARG NODE_VERSION=22.13.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        xz-utils \
    && curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
        | tar -xJf - -C /usr/local --strip-components=1 \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user (UID/GID 1000) — the container must never run as root.
RUN groupadd --gid 1000 enten \
    && useradd --uid 1000 --gid enten --shell /bin/false --create-home enten

WORKDIR /app

# Copy the Python virtual environment from the builder.
COPY --from=builder --chown=enten:enten /app/.venv ./.venv

# Copy source tree and built TypeScript artifacts.
COPY --chown=enten:enten . .
COPY --from=builder --chown=enten:enten /app/packages/ts/app/dist ./packages/ts/app/dist
COPY --from=builder --chown=enten:enten /app/packages/ts/app/node_modules ./packages/ts/app/node_modules
COPY --from=builder --chown=enten:enten /app/node_modules ./node_modules
COPY --from=builder --chown=enten:enten /app/packages/ts/app/package.json ./packages/ts/app/package.json

# Install the Python package itself into the venv so the `enten` console
# script is available for `uv run enten worker`.
COPY --from=ghcr.io/astral-sh/uv:0.5.5@sha256:dc60491f42c9c7228fe2463f551af49a619ebcc9cbd10a470ced7ada63aa25d4 \
    /uv /uvx /bin/
RUN uv pip install --no-deps -e ./packages/python/enten \
    && rm -f /uv /uvx

USER 1000:1000

# Default listening ports. The TS app serves /health on both ports because
# it reuses the same Hono app for the main listener and the metrics listener.
ENV PORT=3000
ENV METRICS_PORT=9090
ENV PYTHONUNBUFFERED=1
ENV NODE_ENV=production
ENV PATH="/app/.venv/bin:${PATH}"

EXPOSE 3000 9090

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python /app/health.py

# Run the Python worker and the TypeScript webhook server in the same
# container. In a larger deployment these are split into separate services.
CMD ["sh", "-c", "enten worker & node packages/ts/app/dist/server.js & wait"]
