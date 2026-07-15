#!/usr/bin/env python3
"""Minimal health checker for the enten production image.

The TS Hono app serves /health on both PORT (3000) and METRICS_PORT
(9090) because it reuses the same app.fetch for both listeners. We
probe METRICS_PORT so the health check stays independent of public
webhook traffic.
"""

from __future__ import annotations

import http.client
import os
import sys

DEFAULT_PORT = 9090
HEALTH_PATH = "/health"
TIMEOUT_SECONDS = 5


def main() -> int:
    port = int(os.environ.get("METRICS_PORT", DEFAULT_PORT))
    try:
        conn = http.client.HTTPConnection("localhost", port, timeout=TIMEOUT_SECONDS)
        conn.request("GET", HEALTH_PATH)
        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        conn.close()
    except Exception as exc:  # pragma: no cover - runtime environment failures
        print(f"health check failed: {exc}", file=sys.stderr)
        return 1

    if response.status != 200:
        print(f"unhealthy: HTTP {response.status} {body}", file=sys.stderr)
        return 1

    if '"status"' not in body or '"ok"' not in body:
        print(f"unhealthy: unexpected body {body!r}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
