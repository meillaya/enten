import { describe, expect, it } from "vitest";

import { createApp } from "../src/server.js";

describe("scaffold", () => {
  it("responds 200 on health", async () => {
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
});
