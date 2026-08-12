import assert from "node:assert/strict";
import test from "node:test";
import { api, ApiError } from "../src/api.ts";

test("API errors preserve a non-JSON response body", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("arXiv upstream returned 429", { status: 502 });
  try {
    await assert.rejects(
      () => api("/api/papers/ingest/arxiv"),
      (error: unknown) => error instanceof ApiError
        && error.status === 502
        && error.message === "arXiv upstream returned 429",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
