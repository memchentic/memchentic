import { getToken, refreshSession } from "./api";
import {
  isUnrestrictedTypes,
  type MemoryRow,
  type MemoryTypeToken,
  type SpaceScope,
} from "./components/ui";

/** Wire shape of one SSE `memory` event (`MemoryEvent` in `events/memory_bus.py`). */
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
  created_by?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

type MembershipLite = { id: string; kind: string };

const SAFETY_POLL_MS = 30_000;

/**
 * Delay before reconnect attempt `attempt` (0-based): 1 s, doubling up to a
 * 30 s cap, scaled by a random factor between 0.5 and 1 so tabs do not
 * reconnect in step.
 */
export function reconnectDelayMs(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(30_000, 1_000 * 2 ** Math.max(0, attempt));
  return Math.round(base * (0.5 + random() / 2));
}

/**
 * Row fields carried by a live event. Creator names are not part of the
 * event, so they are left out and survive the merge in `applyMemoryEvent`.
 */
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
    created_by: event.created_by,
    created_at: event.created_at,
    updated_at: event.updated_at,
  };
}

function eventTypeToken(event: MemoryLiveEvent): string {
  return event.kind === "guide" ? "guide" : (event.type ?? "");
}

function matchesFilters(
  event: MemoryLiveEvent,
  opts: {
    spaceScope: SpaceScope;
    types: MemoryTypeToken[];
    memberships: MembershipLite[];
  },
): boolean {
  if (opts.spaceScope === "personal") {
    const personalIds = new Set(
      opts.memberships.filter((m) => m.kind === "personal").map((m) => m.id),
    );
    if (!personalIds.has(event.space_id)) return false;
  } else if (opts.spaceScope === "group") {
    const groupIds = new Set(
      opts.memberships.filter((m) => m.kind === "group").map((m) => m.id),
    );
    if (!groupIds.has(event.space_id)) return false;
  }

  if (
    !isUnrestrictedTypes(opts.types) &&
    !opts.types.includes(eventTypeToken(event) as MemoryTypeToken)
  ) {
    return false;
  }
  return true;
}

/** Apply a live mutation to the current browse page (offset 0 only for creates). */
export function applyMemoryEvent(
  rows: MemoryRow[],
  event: MemoryLiveEvent,
  opts: {
    spaceScope: SpaceScope;
    types: MemoryTypeToken[];
    memberships: MembershipLite[];
    offset: number;
    pageSize: number;
  },
): MemoryRow[] {
  if (event.action === "deleted") {
    return rows.filter((r) => r.id !== event.id);
  }
  if (!matchesFilters(event, opts)) {
    // Relocate / filter mismatch: drop the row if it no longer belongs in this view.
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

  let attempt = 0;
  const scheduleReconnect = () => {
    if (disposed) return;
    clearReconnect();
    reconnectTimer = setTimeout(() => {
      void runStream();
    }, reconnectDelayMs(attempt));
    attempt += 1;
  };

  async function runStream() {
    if (disposed) return;
    abort?.abort();
    abort = new AbortController();
    let token = getToken();
    if (!token) {
      const outcome = await refreshSession();
      // "denied": the session is gone and api.ts has told the auth provider.
      // Stop; the page unmounts when the user is sent to /sign-in.
      if (disposed || outcome === "denied") return;
      if (outcome !== "ok") {
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
        const outcome = await refreshSession();
        if (outcome !== "denied") scheduleReconnect();
        return;
      }
      if (!res.ok || !res.body) {
        scheduleReconnect();
        return;
      }
      // Connected: the next failure starts the backoff from 1 s again.
      attempt = 0;
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
