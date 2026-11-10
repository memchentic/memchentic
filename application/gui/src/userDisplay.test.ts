import { describe, expect, it } from "vitest";
import { prefersProfileName, resolveDisplayName } from "./userDisplay";

describe("resolveDisplayName", () => {
  it("uses the trimmed display name", () => {
    expect(resolveDisplayName({ email: "ann@example.test", display_name: "  Ann B " })).toBe("Ann B");
  });

  it("falls back to the email local part", () => {
    expect(resolveDisplayName({ email: "ann@example.test", display_name: null })).toBe("ann");
    expect(resolveDisplayName({ email: "ann@example.test", display_name: "   " })).toBe("ann");
  });
});

describe("prefersProfileName", () => {
  it("is false without providers or with only non-profile providers", () => {
    expect(prefersProfileName(undefined)).toBe(false);
    expect(prefersProfileName([])).toBe(false);
    expect(prefersProfileName(["internal"])).toBe(false);
  });

  it("is true when any provider carries a profile name", () => {
    expect(prefersProfileName(["internal", "github"])).toBe(true);
  });
});
