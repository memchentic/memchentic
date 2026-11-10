import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  api,
  apiErrorFromBody,
  errorMessage,
  getToken,
  isUnauthorized,
  refreshSession,
  setSessionLostHandler,
  setToken,
} from "./api";

/** A JSON response as the server sends it. */
function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Replace the global `fetch`; every call gets a fresh result from `answer`. */
function stubFetch(answer: (url: string) => Promise<Response>) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => answer(String(input)));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  setSessionLostHandler(null);
  setToken(null);
});

describe("refreshSession", () => {
  it("reports denied and notifies on 401", async () => {
    const lost = vi.fn();
    setSessionLostHandler(lost);
    setToken("stale-token");
    stubFetch(() => Promise.resolve(jsonResponse(401, { detail: "No active session" })));
    expect(await refreshSession()).toBe("denied");
    expect(lost).toHaveBeenCalledTimes(1);
    expect(getToken()).toBeNull();
  });

  it("reports error on 503 without notifying", async () => {
    const lost = vi.fn();
    setSessionLostHandler(lost);
    stubFetch(() => Promise.resolve(jsonResponse(503, { detail: "unavailable" })));
    expect(await refreshSession()).toBe("error");
    expect(lost).not.toHaveBeenCalled();
  });

  it("reports error when fetch rejects", async () => {
    stubFetch(() => Promise.reject(new TypeError("network down")));
    expect(await refreshSession()).toBe("error");
  });

  it("stores the token on 200", async () => {
    const fetchMock = stubFetch(() =>
      Promise.resolve(jsonResponse(200, { access_token: "fresh-token" })),
    );
    expect(await refreshSession()).toBe("ok");
    expect(getToken()).toBe("fresh-token");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("api", () => {
  it("throws an ApiError that carries status, code and duplicate id", async () => {
    stubFetch(() =>
      Promise.resolve(
        jsonResponse(409, { detail: "Near-duplicate exists", code: "duplicate", duplicate_id: "m1" }),
      ),
    );
    const err = await api("/api/memories", { method: "POST", body: "{}" }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    const apiErr = err as ApiError;
    expect(apiErr.status).toBe(409);
    expect(apiErr.code).toBe("duplicate");
    expect(apiErr.duplicateId).toBe("m1");
    expect(apiErr.message).toBe("Near-duplicate exists");
    expect(isUnauthorized(apiErr)).toBe(false);
  });

  it("reports a lost session when a fresh token is still rejected", async () => {
    const lost = vi.fn();
    setSessionLostHandler(lost);
    stubFetch((url) =>
      Promise.resolve(
        url === "/api/auth/refresh"
          ? jsonResponse(200, { access_token: "fresh-token" })
          : jsonResponse(401, { detail: "Not authenticated" }),
      ),
    );
    const err = await api("/api/me").catch((e: unknown) => e);
    expect(isUnauthorized(err)).toBe(true);
    expect(lost).toHaveBeenCalledTimes(1);
  });

  it("keeps the session on a 503 refresh", async () => {
    const lost = vi.fn();
    setSessionLostHandler(lost);
    stubFetch((url) =>
      Promise.resolve(
        url === "/api/auth/refresh"
          ? jsonResponse(503, { detail: "unavailable" })
          : jsonResponse(401, { detail: "Not authenticated" }),
      ),
    );
    await expect(api("/api/me")).rejects.toBeInstanceOf(ApiError);
    expect(lost).not.toHaveBeenCalled();
  });
});

describe("errorMessage", () => {
  it("reads a string detail", () => {
    expect(errorMessage({ detail: "title is required", code: "invalid" }, "x")).toBe(
      "title is required",
    );
  });

  it("formats FastAPI validation items instead of raw JSON", () => {
    const body = {
      detail: [
        { loc: ["body", "title"], msg: "Field required", type: "missing" },
        { loc: ["query", "limit"], msg: "Input should be less than 201" },
      ],
    };
    expect(errorMessage(body, "x")).toBe(
      "title: Field required; limit: Input should be less than 201",
    );
  });

  it("falls back for a non-object body", () => {
    expect(errorMessage(null, "Bad Gateway")).toBe("Bad Gateway");
    expect(errorMessage({ error: "boom" }, "x")).toBe("boom");
  });

  it("ignores an unknown code", () => {
    const err = apiErrorFromBody({ detail: "nope", code: "weird" }, 400, "x");
    expect(err.code).toBeNull();
    expect(err.duplicateId).toBeNull();
  });
});
