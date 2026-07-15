FROM python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28 AS builder

COPY build-requirements.lock /tmp/build-requirements.lock
COPY requirements.lock /tmp/requirements.lock
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --require-hashes --no-cache-dir \
        --requirement /tmp/build-requirements.lock \
    && /opt/venv/bin/pip install --require-hashes --no-build-isolation --no-cache-dir \
        --requirement /tmp/requirements.lock \
    && rm -rf \
        /opt/venv/bin/pip* \
        /opt/venv/bin/wheel \
        /opt/venv/lib/python3.12/site-packages/pip* \
        /opt/venv/lib/python3.12/site-packages/wheel* \
        /root/.cache

FROM python:3.12-slim@sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28 AS runtime

LABEL org.opencontainers.image.title="enten HTTPie verifier" \
      org.opencontainers.image.source="https://github.com/httpie/cli" \
      org.opencontainers.image.version="3.2.4" \
      org.opencontainers.image.revision="2105caa49bae87c5809c274e407619a0de2639d1" \
      io.enten.source.tree="5be8d10a92efc928460e86f1ce6dcabbea1a728a" \
      io.enten.source.digest="43ea1a4bd1c6b5725df2775f4665f092305f16119c806b84de51e5ee5261268e" \
      io.enten.test.selector="tests/test_parser_schema.py" \
      io.enten.base.digest="sha256:c3d81d25b3154142b0b42eb1e61300024426268edeb5b5a26dd7ddf64d9daf28"

COPY --from=builder /opt/venv /opt/venv
COPY verify.py /opt/verifier/verify.py

RUN rm -rf \
        /usr/bin/apt* \
        /usr/bin/dpkg* \
        /usr/sbin/dpkg* \
        /usr/local/bin/pip* \
        /usr/local/lib/python3.12/site-packages/pip* \
        /usr/local/lib/python3.12/ensurepip \
        /etc/apt \
        /var/cache/apt \
        /var/lib/apt \
        /var/lib/dpkg \
    && test ! -e /usr/bin/gcc \
    && test ! -e /usr/bin/g++ \
    && test ! -e /usr/bin/cc \
    && test ! -e /usr/bin/make

ENV PATH="/opt/venv/bin:/usr/local/bin:/usr/bin:/bin" \
    HOME="/workspace" \
    PYTHONDONTWRITEBYTECODE="1" \
    PYTHONUNBUFFERED="1" \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"

WORKDIR /workspace
USER 1000:1000
ENTRYPOINT ["/opt/venv/bin/python", "/opt/verifier/verify.py"]
