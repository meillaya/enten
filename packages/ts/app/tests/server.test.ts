import { afterEach, describe, expect, it, vi } from "vitest";

import { createApp, loadConfig, start } from "../src/server.js";

describe("createApp", () => {
  it("wires /health, /metrics and /webhook", async () => {
    const { app } = createApp({
      secret: "secret",
      databaseUrl: "postgresql://unused",
    });

    const health = await app.request("/health");
    expect(health.status).toBe(200);

    const metrics = await app.request("/metrics");
    expect(metrics.status).toBe(200);

    const webhook = await app.request("/webhook", { method: "POST", body: "{}" });
    expect(webhook.status).toBe(401);
  });
});

describe("loadConfig", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns a parsed Config for a valid environment", () => {
    const config = loadConfig({
      GITHUB_APP_ID: "123",
      GITHUB_WEBHOOK_SECRET: "secret",
      DATABASE_URL: "postgresql://db",
    });
    expect(config.GITHUB_APP_ID).toBe("123");
    expect(config.PORT).toBe("3000");
  });

  it("calls process.exit(1) when the environment is invalid", () => {
    const exitSpy = vi.spyOn(process, "exit").mockImplementation(() => {
      throw new Error("process.exit");
    });

    expect(() => loadConfig({})).toThrow("process.exit");
    expect(exitSpy).toHaveBeenCalledWith(1);
  });
});

describe("start", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("serves the app on PORT and METRICS_PORT", () => {
    const serve = vi.fn();
    start({ serve });

    expect(serve).toHaveBeenCalledTimes(2);
    expect(serve).toHaveBeenNthCalledWith(
      1,
      expect.objectContaining({ port: 3000 }),
    );
    expect(serve).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ port: 9091 }),
    );
  });
});
