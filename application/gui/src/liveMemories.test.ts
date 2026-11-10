import { describe, expect, it } from "vitest";
import { applyMemoryEvent, reconnectDelayMs, type MemoryLiveEvent } from "./liveMemories";

describe("reconnectDelayMs", () => {
  it("doubles to a 30 s cap with jitter", () => {
    expect(reconnectDelayMs(0, () => 1)).toBe(1000);
    expect(reconnectDelayMs(0, () => 0)).toBe(500);
    expect(reconnectDelayMs(3, () => 1)).toBe(8000);
    expect(reconnectDelayMs(10, () => 1)).toBe(30000);
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
  });
});
