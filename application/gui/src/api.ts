// Access tokens live only in memory; the HttpOnly session cookie restores them.
let accessToken: string | null = null;
let refreshing: Promise<boolean> | null = null;

const GUI_HEADERS = { "X-Memchentic-Client": "gui" };

export function getToken(): string | null {
  return accessToken;
}

export function setToken(token: string | null) {
  accessToken = token;
}

/** Trade the session cookie for a fresh access token; concurrent callers share one request. */
export function refreshAccessToken(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const res = await fetch("/api/auth/refresh", {
        method: "POST",
        headers: GUI_HEADERS,
        credentials: "include",
      });
      if (!res.ok) {
        accessToken = null;
        return false;
      }
      accessToken = ((await res.json()) as { access_token: string }).access_token;
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
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

export async function api<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  let res = await send(path, options);
  if (res.status === 401 && !path.startsWith("/api/auth/") && (await refreshAccessToken())) {
    res = await send(path, options);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = data.detail || data.error || JSON.stringify(data);
    } catch {
      /* ignore */
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}
