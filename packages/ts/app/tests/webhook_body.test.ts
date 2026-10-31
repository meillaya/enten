import { afterEach, describe, expect, it, vi } from "vitest";

import { parseJson, readBoundedBody } from "../src/routes/webhook_body.js";

afterEach(() => vi.restoreAllMocks());

describe("readBoundedBody", () => {
  it("treats a request without a body as empty", async () => {
    // Given
    const request = new Request("http://localhost/webhook", { method: "POST" });

    // When
    const result = await readBoundedBody(request, 16);

    // Then
    expect(result).toEqual({ kind: "ok", body: "" });
  });

  it("ignores a non-numeric content length", async () => {
    // Given
    const request = new Request("http://localhost/webhook", {
      method: "POST",
      body: "{}",
    });
    vi.spyOn(request.headers, "get").mockReturnValue("not-a-number");

    // When
    const result = await readBoundedBody(request, 16);

    // Then
    expect(result).toEqual({ kind: "ok", body: "{}" });
  });

  it("rethrows a decoder failure that is not a TypeError", async () => {
    // Given
    const request = new Request("http://localhost/webhook", {
      method: "POST",
      body: "{}",
    });
    vi.spyOn(TextDecoder.prototype, "decode").mockImplementation(() => {
      throw new Error("decoder failed");
    });

    // When
    const read = readBoundedBody(request, 16);

    // Then
    await expect(read).rejects.toThrow("decoder failed");
  });
});

describe("parseJson", () => {
  it("rethrows a parser failure that is not a SyntaxError", () => {
    // Given
    vi.spyOn(JSON, "parse").mockImplementation(() => {
      throw new Error("parser failed");
    });

    // When
    const parse = () => parseJson("{}");

    // Then
    expect(parse).toThrow("parser failed");
  });
});
