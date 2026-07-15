import { describe, expect, it } from "vitest";

import { createApp } from "../src/server.js";

describe("coverage gate", () => {
  it("exposes a callable Hono app", async () => {
    // Given
    const created = createApp({
      secret: "secret",
      databaseUrl: "postgresql://unused",
    });

    // When
    const fetchType = typeof created.app.fetch;

    // Then
    expect(fetchType).toBe("function");
    await created.sql.end();
  });
});
