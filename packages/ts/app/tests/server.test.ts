import { createServer } from "node:http";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConfigSchema } from "../src/config.js";
import {
  ConfigurationError,
  createApp,
  loadConfig,
  start,
  toLoggedError,
} from "../src/server.js";

describe("createApp", () => {
  it("wires health without opening a listener", async () => {
    // Given
    const created = createApp({
      secret: "secret",
      databaseUrl: "postgresql://unused",
    });

    // When
    const response = await created.app.request("/health");

    // Then
    expect(response.status).toBe(200);
    await created.sql.end();
  });

  it("serves Prometheus metrics only on the metrics listener", async () => {
    // Given
    const created = createApp({
      secret: "secret",
      databaseUrl: "postgresql://unused",
    });

    // When
    const publicResponse = await created.app.request("/metrics");
    const metricsResponse = await created.metricsApp.request("/metrics");

    // Then
    expect(publicResponse.status).toBe(404);
    expect(metricsResponse.status).toBe(200);
    expect(metricsResponse.headers.get("content-type")).toContain("text/plain");
    await created.sql.end();
  });

  it("reports readiness from the injected database check", async () => {
    // Given
    const created = createApp({
      secret: "secret",
      databaseUrl: "postgresql://unused",
      readyCheck: async () => true,
    });

    // When
    const response = await created.app.request("/ready");

    // Then
    expect(response.status).toBe(200);
    await created.sql.end();
  });

  it("returns 503 when the database check fails", async () => {
    // Given
    const created = createApp({
      secret: "secret",
      databaseUrl: "postgresql://unused",
      readyCheck: async () => false,
    });

    // When
    const response = await created.app.request("/ready");

    // Then
    expect(response.status).toBe(503);
    await created.sql.end();
  });

  it("returns 503 when the database check throws", async () => {
    // Given
    const created = createApp({
      secret: "secret",
      databaseUrl: "postgresql://unused",
      readyCheck: async () => {
        throw new Error("database unavailable");
      },
    });

    // When
    const response = await created.app.request("/ready");

    // Then
    expect(response.status).toBe(503);
    await created.sql.end();
  });
});

describe("loadConfig", () => {
  it("returns a parsed config for a valid environment", () => {
    // Given
    const environment = {
      GITHUB_APP_ID: "123",
      GITHUB_WEBHOOK_SECRET: "test-webhook-secret-0123456789abcdef",
      DATABASE_URL: "postgresql://db",
    };

    // When
    const config = loadConfig(environment);

    // Then
    expect(config.PORT).toBe("3000");
  });

  it("throws a typed error instead of exiting for invalid configuration", () => {
    // Given
    const environment = {};

    // When
    const load = () => loadConfig(environment);

    // Then
    expect(load).toThrow(ConfigurationError);
  });
});

describe("start", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shares one listener when both ports match", async () => {
    // Given
    const server = createServer();
    const serve = vi.fn(() => server.listen(0));
    const config = ConfigSchema.parse({
      GITHUB_APP_ID: "123",
      GITHUB_WEBHOOK_SECRET: "test-webhook-secret-0123456789abcdef",
      DATABASE_URL: "postgresql://unused",
      PORT: "3000",
      METRICS_PORT: "3000",
      OTEL_ENDPOINT: "http://localhost:4317",
    });

    // When
    const runtime = await start({
      config,
      serve,
      initTelemetry: () => ({ shutdown: async () => undefined }),
    });
    const metrics = await runtime.app.request("/metrics");
    await runtime.shutdown();

    // Then
    expect(serve).toHaveBeenCalledTimes(1);
    expect(metrics.status).toBe(200);
  });

  it("drains both listeners, Postgres, and telemetry exactly once", async () => {
    // Given
    const servers = [createServer(), createServer()];
    const serve = vi
      .fn()
      .mockImplementationOnce(() => servers[0]?.listen(0))
      .mockImplementationOnce(() => servers[1]?.listen(0));
    const telemetryShutdown = vi.fn(async () => undefined);
    const config = ConfigSchema.parse({
      GITHUB_APP_ID: "123",
      GITHUB_WEBHOOK_SECRET: "test-webhook-secret-0123456789abcdef",
      DATABASE_URL: "postgresql://unused",
      PORT: "3000",
      METRICS_PORT: "9091",
      OTEL_ENDPOINT: "http://localhost:4317",
    });

    // When
    const runtime = await start({
      config,
      serve,
      initTelemetry: () => ({ shutdown: telemetryShutdown }),
    });
    await runtime.shutdown();
    await runtime.shutdown();

    // Then
    expect(serve).toHaveBeenCalledTimes(2);
    expect(servers.every((server) => !server.listening)).toBe(true);
    expect(telemetryShutdown).toHaveBeenCalledTimes(1);
  });

  it("normalizes Error and non-Error values for logging", () => {
    // Given
    const error = new Error("boom");

    // When
    const loggedError = toLoggedError(error);
    const loggedValue = toLoggedError("boom");

    // Then
    expect(loggedError.error).toBe(error);
    expect(loggedValue.error).toBe("boom");
  });
});
