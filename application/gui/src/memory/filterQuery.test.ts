import { describe, expect, it } from "vitest";
import { formatMemoryQuery, parseMemoryQuery } from "./filterQuery";

describe("parseMemoryQuery", () => {
  it("splits facet tokens from free text", () => {
    expect(
      parseMemoryQuery("kind:entry type:fact tag:ops space:personal deploy helm"),
    ).toEqual({
      q: "deploy helm",
      kind: "entry",
      types: ["fact"],
      tags: ["ops"],
      spaceScope: "personal",
    });
  });

  it("treats unknown keys as free text", () => {
    expect(parseMemoryQuery("project:alpha hello")).toEqual({
      q: "project:alpha hello",
      kind: "",
      types: [],
      tags: [],
      spaceScope: "",
    });
  });
});

describe("formatMemoryQuery", () => {
  it("round-trips structured facets", () => {
    const raw = formatMemoryQuery({
      q: "deploy",
      kind: "guide",
      types: ["guide"],
      tags: ["ops"],
      spaceScope: "group",
    });
    expect(raw).toBe("kind:guide type:guide tag:ops space:group deploy");
    expect(parseMemoryQuery(raw)).toEqual({
      q: "deploy",
      kind: "guide",
      types: ["guide"],
      tags: ["ops"],
      spaceScope: "group",
    });
  });
});
