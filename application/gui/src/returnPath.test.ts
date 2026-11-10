import { describe, expect, it } from "vitest";
import { safeReturnPath } from "./returnPath";

describe("safeReturnPath", () => {
  it("returns an application path with its query", () => {
    expect(safeReturnPath({ from: "/entries/1?x=1" })).toBe("/entries/1?x=1");
  });

  it("returns / for anything that is not an application path", () => {
    const refused: unknown[] = [
      { from: "//evil.example" },
      { from: "/\\evil.example" },
      { from: "https://evil.example" },
      { from: "/sign-in" },
      { from: "/sign-in?error=x" },
      { from: 5 },
      {},
      null,
      "text",
    ];

    for (const state of refused) {
      expect(safeReturnPath(state)).toBe("/");
    }
  });
});
