export type BodyReadResult =
  | { readonly kind: "ok"; readonly body: string }
  | { readonly kind: "too_large" }
  | { readonly kind: "invalid_encoding" };

export async function readBoundedBody(
  request: Request,
  maxBytes: number,
): Promise<BodyReadResult> {
  const contentLength = request.headers.get("content-length");
  if (
    contentLength !== null &&
    /^\d+$/.test(contentLength) &&
    Number(contentLength) > maxBytes
  ) {
    return { kind: "too_large" };
  }

  if (request.body === null) {
    return { kind: "ok", body: "" };
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let totalBytes = 0;
  while (true) {
    const next = await reader.read();
    if (next.done) {
      break;
    }
    totalBytes += next.value.byteLength;
    if (totalBytes > maxBytes) {
      await reader.cancel();
      return { kind: "too_large" };
    }
    chunks.push(next.value);
  }

  const combined = new Uint8Array(totalBytes);
  let offset = 0;
  for (const chunk of chunks) {
    combined.set(chunk, offset);
    offset += chunk.byteLength;
  }

  try {
    return {
      kind: "ok",
      body: new TextDecoder("utf-8", { fatal: true }).decode(combined),
    };
  } catch (error: unknown) {
    if (error instanceof TypeError) {
      return { kind: "invalid_encoding" };
    }
    throw error;
  }
}

export function parseJson(body: string):
  | { readonly kind: "ok"; readonly value: unknown }
  | { readonly kind: "malformed" } {
  try {
    const value: unknown = JSON.parse(body);
    return { kind: "ok", value };
  } catch (error: unknown) {
    if (error instanceof SyntaxError) {
      return { kind: "malformed" };
    }
    throw error;
  }
}
