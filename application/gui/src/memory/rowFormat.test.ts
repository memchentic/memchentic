import { describe, expect, it } from "vitest";
import type { Me } from "../auth";
import { canForgetMemory, distinctSnippet, formatMemoryOwner } from "./rowFormat";
import type { MemoryRow } from "./types";

const member: Me = {
  id: "u1",
  subject: "u1",
  is_admin: false,
  personal_space_id: "p1",
  memberships: [
    { id: "p1", name: "Personal", kind: "personal", created_by: "u1" },
    { id: "g1", name: "Mine", kind: "group", created_by: "u1" },
    { id: "g2", name: "Theirs", kind: "group", created_by: "u9" },
    { id: "g3", name: "Older server", kind: "group" },
  ],
};

function row(extra: Partial<MemoryRow>): MemoryRow {
  return { id: "m1", kind: "entry", type: "note", title: "T", tags: [], ...extra };
}

describe("canForgetMemory", () => {
  it("hides Forget from a plain member on a peer's memory", () => {
    expect(canForgetMemory({ space_id: "g2", created_by: "u2" }, member)).toBe(false);
  });

  it("allows the creator, the space creator and administrators", () => {
    expect(canForgetMemory({ space_id: "g2", created_by: "u1" }, member)).toBe(true);
    expect(canForgetMemory({ space_id: "g1", created_by: "u2" }, member)).toBe(true);
    expect(canForgetMemory({ space_id: "g2", created_by: "u2" }, { ...member, is_admin: true })).toBe(
      true,
    );
  });

  it("leaves the decision to the server when it cannot tell", () => {
    expect(canForgetMemory({ space_id: "g3", created_by: "u2" }, member)).toBe(true);
    expect(canForgetMemory({ space_id: "unknown", created_by: "u2" }, member)).toBe(true);
    expect(canForgetMemory({ space_id: "g1", created_by: "u2" }, null)).toBe(false);
  });
});

describe("formatMemoryOwner", () => {
  it("prefers the display name, then the email", () => {
    expect(
      formatMemoryOwner(row({ created_by_display_name: " Ann ", created_by_email: "ann@example.test" })),
    ).toBe("Ann");
    expect(formatMemoryOwner(row({ created_by_email: "ann@example.test", created_by: "abcdef123456" }))).toBe(
      "ann@example.test",
    );
  });

  it("names the caller for their own rows", () => {
    const me = { id: "abcdef123456", email: "me@example.test", display_name: "Me" };
    expect(formatMemoryOwner(row({ created_by: "abcdef123456" }), me)).toBe("Me");
    expect(formatMemoryOwner(row({ created_by: "abcdef123456" }), { ...me, display_name: null })).toBe(
      "me@example.test",
    );
  });

  it("falls back to a short id, then a dash", () => {
    expect(formatMemoryOwner(row({ created_by: "abcdef123456" }))).toBe("abcdef12");
    expect(formatMemoryOwner(row({}))).toBe("—");
  });
});

describe("distinctSnippet", () => {
  it("drops a missing snippet or one that repeats the title", () => {
    expect(distinctSnippet("Hello world")).toBeNull();
    expect(distinctSnippet("Hello world", "hello  world…")).toBeNull();
    expect(distinctSnippet("Hello", "Hello world and more")).toBeNull();
  });

  it("keeps a snippet that adds text", () => {
    expect(distinctSnippet("Title", "Something else entirely")).toBe("Something else entirely");
  });
});
