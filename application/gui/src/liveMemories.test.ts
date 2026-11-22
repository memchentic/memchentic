import { afterEach, describe, expect, it, vi } from "vitest";
import {
  applyMemoryEvent,
  createDebounced,
  reconnectDelayMs,
  type MemoryLiveEvent,
} from "./liveMemories";

describe("reconnectDelayMs", () => {
  it("doubles to a 30 s cap with jitter", () => {
    expect(reconnectDelayMs(0, () => 1)).toBe(1000);
    expect(reconnectDelayMs(0, () => 0)).toBe(500);
    expect(reconnectDelayMs(3, () => 1)).toBe(8000);
    expect(reconnectDelayMs(10, () => 1)).toBe(30000);
  });
});

describe("createDebounced", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("fires once after the quiet window", () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const { trigger, cancel } = createDebounced(fn, 400);
    trigger();
    trigger();
    trigger();
    expect(fn).not.toHaveBeenCalled();
    vi.advanceTimersByTime(399);
    expect(fn).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(fn).toHaveBeenCalledTimes(1);
    cancel();
  });

  it("cancel drops a pending call", () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const { trigger, cancel } = createDebounced(fn, 400);
    trigger();
    cancel();
    vi.advanceTimersByTime(500);
    expect(fn).not.toHaveBeenCalled();
  });
});

describe("applyMemoryEvent", () => {
  it("keeps the creator name the event does not carry", () => {
    const row = {
      id: "m1",
      space_id: "g1",
      kind: "entry",
      type: "note",
      title: "Old",
      tags: [],
      created_by: "u2",
      created_by_email: "u2@example.test",
      created_by_display_name: "User Two",
    };
    const event: MemoryLiveEvent = {
      action: "updated",
      id: "m1",
      space_id: "g1",
      space_name: "Platform",
      space_kind: "group",
      kind: "entry",
      type: "note",
      title: "New",
      tags: [],
      created_by: "u2",
    };
    const [next] = applyMemoryEvent([row], event, {
      spaceScope: "",
      types: ["note"],
      memberships: [{ id: "g1", kind: "group" }],
      offset: 0,
      pageSize: 50,
    });
    expect(next.title).toBe("New");
    expect(next.created_by_display_name).toBe("User Two");
    expect(next.created_by_email).toBe("u2@example.test");
    expect(next.space_name).toBe("Platform");
    expect(next.space_kind).toBe("group");
  });
});
