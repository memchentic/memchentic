import { describe, expect, it } from "vitest";
import { spaceLabel, writableSpaces, type Me } from "./auth";

// An administrator as an older server reports it: every user's personal
// space is in `memberships`.
const me: Me = {
  id: "u1",
  subject: "u1",
  is_admin: true,
  personal_space_id: "p1",
  memberships: [
    { id: "p1", name: "Personal", kind: "personal" },
    { id: "p2", name: "Personal", kind: "personal" },
    { id: "g1", name: "Platform", kind: "group" },
  ],
};

describe("writableSpaces", () => {
  it("keeps only the caller's personal space", () => {
    expect(writableSpaces(me).map((s) => s.id)).toEqual(["p1", "g1"]);
  });

  it("returns [] for null", () => {
    expect(writableSpaces(null)).toEqual([]);
  });
});

describe("spaceLabel", () => {
  it("names other users' personal spaces", () => {
    expect(spaceLabel(me, "p1")).toBe("Personal");
    expect(spaceLabel(me, "p2")).toBe("Personal (another user)");
    expect(spaceLabel(me, "g1")).toBe("Platform");
    expect(spaceLabel(me, "missing")).toBe("");
  });
});
