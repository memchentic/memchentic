import { describe, expect, it } from "vitest";
import type { Me } from "../auth";
import { canForgetMemory } from "./ui";

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
