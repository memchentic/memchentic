import { getToken, refreshAccessToken } from "./api";
import type { MemoryRow } from "./components/ui";

export type MemoryLiveEvent = {
  action: "created" | "updated" | "deleted";
  id: string;
  space_id: string;
  kind: string;
  type: string | null;
  title: string;
  tags: string[];
  snippet?: string;
  confidence?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
};

const SAFETY_POLL_MS = 30_000;

function eventToRow(event: MemoryLiveEvent): MemoryRow {
  return {
    id: event.id,
    space_id: event.space_id,
    kind: event.kind,
    type: event.type,
    title: event.title,
    tags: event.tags ?? [],
    snippet: event.snippet,
    confidence: event.confidence ?? undefined,
    created_at: event.created_at,
    updated_at: event.updated_at,
  };
}

function matchesFilters(
  event: MemoryLiveEvent,
  space: string,
  kind: string,
): boolean {
  if (space && event.space_id !== space) return false;
  if (kind && event.kind !== kind) return false;
  return true;
}

/** Apply a live mutation to the current browse page (offset 0 only for creates). */
export function applyMemoryEvent(
  rows: MemoryRow[],
  event: MemoryLiveEvent,
  opts: { space: string; kind: string; offset: number; pageSize: number },
): MemoryRow[] {
  if (event.action === "deleted") {
    return rows.filter((r) => r.id !== event.id);
  }
  if (!matchesFilters(event, opts.space, opts.kind)) {
    // Relocate / kind mismatch: drop the row if it no longer belongs in this view.
    return rows.filter((r) => r.id !== event.id);
  }
  const next = eventToRow(event);
  const idx = rows.findIndex((r) => r.id === event.id);
  if (event.action === "updated") {
    if (idx >= 0) {
      const copy = rows.slice();
      copy[idx] = { ...rows[idx], ...next };
      return copy;
    }
    // Updated row not on this page — ignore (safety poll will catch if needed).
    return rows;
  }
  // created
  if (opts.offset !== 0) return rows;
  if (idx >= 0) {
    const copy = rows.slice();
    copy[idx] = { ...rows[idx], ...next };
    return copy;
  }
  return [next, ...rows].slice(0, opts.pageSize);
}

type LiveHandlers = {
  onEvent: (event: MemoryLiveEvent) => void;
  onSafetyPoll: () => void;
};

/**
 * Open an authenticated SSE stream to `/api/events/memories` and a 30s safety poll.
 * Returns a dispose function.
 */
export function startMemoryLive(handlers: LiveHandlers): () => void {
  let disposed = false;
  let abort: AbortController | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  const clearReconnect = () => {
    if (reconnectTimer != null) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  const scheduleReconnect = () => {
    if (disposed) return;
    clearReconnect();
    reconnectTimer = setTimeout(() => {
      void runStream();
    }, 2000);
  };

  async function runStream() {
    if (disposed) return;
    abort?.abort();
    abort = new AbortController();
    let token = getToken();
    if (!token) {
      const ok = await refreshAccessToken();
      if (!ok || disposed) {
        scheduleReconnect();
        return;
      }
      token = getToken();
    }
    try {
      const res = await fetch("/api/events/memories", {
        method: "GET",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "text/event-stream",
          "X-Memchentic-Client": "gui",
        },
        credentials: "include",
        signal: abort.signal,
      });
      if (res.status === 401) {
        await refreshAccessToken();
        scheduleReconnect();
        return;
      }
      if (!res.ok || !res.body) {
        scheduleReconnect();
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let eventName = "message";
      while (!disposed) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n");
        buffer = parts.pop() ?? "";
        for (const rawLine of parts) {
          const line = rawLine.replace(/\r$/, "");
          if (line.startsWith("event:")) {
            eventName = line.slice(6).trim();
          } else if (line.startsWith("data:") && eventName === "memory") {
            const payload = line.slice(5).trim();
            try {
              handlers.onEvent(JSON.parse(payload) as MemoryLiveEvent);
            } catch {
              /* ignore malformed */
            }
          } else if (line === "") {
            eventName = "message";
          }
        }
      }
    } catch {
      /* aborted or network */
    }
    if (!disposed) scheduleReconnect();
  }

  void runStream();
  pollTimer = setInterval(() => {
    if (!disposed) handlers.onSafetyPoll();
  }, SAFETY_POLL_MS);

  return () => {
    disposed = true;
    abort?.abort();
    clearReconnect();
    if (pollTimer != null) clearInterval(pollTimer);
  };
}
