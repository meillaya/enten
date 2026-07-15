import { createServer } from "node:http";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ConfigSchema } from "../src/config.js";
import {
  ConfigurationError,
  createApp,
  loadConfig,
  start,
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

  it("serves Prometheus metrics through the application route", async () => {
    // Given
    const created = createApp({
      secret: "secret",
      databaseUrl: "postgresql://unused",
    });

    // When
    const response = await created.app.request("/metrics");

    // Then
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/plain");
    await created.sql.end();
  });
});

describe("loadConfig", () => {
  it("returns a parsed config for a valid environment", () => {
    // Given
    const environment = {
      GITHUB_APP_ID: "123",
      GITHUB_WEBHOOK_SECRET: "secret",
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
      GITHUB_WEBHOOK_SECRET: "secret",
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
});
