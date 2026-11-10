// Access tokens live only in memory; the HttpOnly session cookie restores them.
let accessToken: string | null = null;

/** Result of asking the server for a new access token. */
export type RefreshOutcome = "ok" | "denied" | "error";

let refreshing: Promise<RefreshOutcome> | null = null;
let onSessionLost: (() => void) | null = null;

const GUI_HEADERS = { "X-Memchentic-Client": "gui" };

export function getToken(): string | null {
  return accessToken;
}

export function setToken(token: string | null) {
  accessToken = token;
}

/** Machine-readable error kind the API sends next to `detail`. */
export type ApiErrorCode = "invalid" | "not_found" | "forbidden" | "conflict" | "duplicate";

const API_ERROR_CODES: readonly string[] = [
  "invalid",
  "not_found",
  "forbidden",
  "conflict",
  "duplicate",
];

/** Error thrown by `api()` for a non-2xx response; `status` is the HTTP status code. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: ApiErrorCode | null;
  /** Id of the existing memory when `code` is "duplicate". */
  readonly duplicateId: string | null;

  constructor(
    message: string,
    status: number,
    code: ApiErrorCode | null = null,
    duplicateId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.duplicateId = duplicateId;
  }
}

/** True when `err` is an `ApiError` for HTTP 401. */
export function isUnauthorized(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401;
}

/**
 * Register the callback that runs when the server says the browser session
 * is gone: the refresh answered 401, or a request is still 401 after a
 * successful refresh. Pass null to unregister.
 */
export function setSessionLostHandler(handler: (() => void) | null) {
  onSessionLost = handler;
}

function sessionLost() {
  accessToken = null;
  onSessionLost?.();
}

/**
 * Trade the session cookie for a fresh access token; concurrent callers
 * share one request. "denied" means the server rejected the session (HTTP
 * 401). "error" means the request failed for another reason and may be
 * retried later.
 */
export function refreshSession(): Promise<RefreshOutcome> {
  refreshing ??= (async (): Promise<RefreshOutcome> => {
    try {
      const res = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: GUI_HEADERS,
        credentials: "include",
      });
      if (res.status === 401) {
        sessionLost();
        return "denied";
      }
      if (!res.ok) return "error";
      accessToken = ((await res.json()) as { access_token: string }).access_token;
      return "ok";
    } catch {
      return "error";
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

/** Boolean form of `refreshSession`, kept for existing callers. */
export async function refreshAccessToken(): Promise<boolean> {
  return (await refreshSession()) === "ok";
}

async function send(path: string, options: RequestInit): Promise<Response> {
  const headers = new Headers(options.headers || {});
  if (!headers.has("Content-Type") && options.body) {
    headers.set("Content-Type", "application/json");
  }
  for (const [k, v] of Object.entries(GUI_HEADERS)) headers.set(k, v);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  return fetch(path, { ...options, headers, credentials: "include" });
}

/** One FastAPI validation item (`{loc, msg, …}`) as "field: message". */
function validationItemText(item: unknown): string {
  if (typeof item === "string") return item;
  if (!item || typeof item !== "object") return String(item);
  const { loc, msg } = item as { loc?: unknown; msg?: unknown };
  const text = typeof msg === "string" && msg ? msg : "invalid value";
  const field = Array.isArray(loc) && loc.length > 0 ? String(loc[loc.length - 1]) : "";
  return field ? `${field}: ${text}` : text;
}

/**
 * Turn an error response body into one readable line.
 *
 * Handles `{"detail": "text"}`, `{"detail": [validation items]}`,
 * `{"detail": {…}}` and `{"error": "text"}`; returns `fallback` when the
 * body is not an object.
 */
export function errorMessage(data: unknown, fallback: string): string {
  if (!data || typeof data !== "object") return fallback;
  const { detail, error } = data as { detail?: unknown; error?: unknown };
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail.map(validationItemText).join("; ");
  }
  if (detail !== undefined && detail !== null && typeof detail === "object") {
    return JSON.stringify(detail);
  }
  if (typeof error === "string" && error) return error;
  return JSON.stringify(data);
}

/** Build the `ApiError` for a non-2xx response body (`{detail, code, duplicate_id?}`). */
export function apiErrorFromBody(data: unknown, status: number, fallback: string): ApiError {
  const message = errorMessage(data, fallback);
  if (!data || typeof data !== "object") return new ApiError(message, status);
  const { code, duplicate_id } = data as { code?: unknown; duplicate_id?: unknown };
  const knownCode =
    typeof code === "string" && API_ERROR_CODES.includes(code) ? (code as ApiErrorCode) : null;
  const duplicateId = typeof duplicate_id === "string" && duplicate_id ? duplicate_id : null;
  return new ApiError(message, status, knownCode, duplicateId);
}

export async function api<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  let res = await send(path, options);
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    const outcome = await refreshSession();
    if (outcome === "ok") {
      res = await send(path, options);
      // A fresh token that is still rejected means the account is gone.
      if (res.status === 401) sessionLost();
    }
  }
  if (!res.ok) {
    const fallback = res.statusText || `HTTP ${res.status}`;
    let data: unknown = null;
    try {
      data = await res.json();
    } catch {
      /* The body is not JSON; keep the status text. */
    }
    throw apiErrorFromBody(data, res.status, fallback);
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
