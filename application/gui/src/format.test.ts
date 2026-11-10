import { describe, expect, it } from "vitest";
import { errorMessage } from "./errors";
import { formatDate, formatDateTime, formatWhen } from "./format";

const ISO = "2026-03-04T05:06:00Z";

describe("format", () => {
  it("renders a dash for missing or unparseable values", () => {
    for (const fn of [formatWhen, formatDateTime, formatDate]) {
      expect(fn(undefined)).toBe("—");
      expect(fn(null)).toBe("—");
      expect(fn("")).toBe("—");
      expect(fn("not a date")).toBe("—");
    }
  });

  it("formats a valid timestamp with the browser locale", () => {
    const date = new Date(ISO);
    expect(formatDateTime(ISO)).toBe(date.toLocaleString());
    expect(formatDate(ISO)).toBe(date.toLocaleDateString());
    expect(formatWhen(ISO)).toBe(
      date.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }),
    );
  });
});

describe("errorMessage", () => {
  it("uses the message of an Error", () => {
    expect(errorMessage(new Error("boom"))).toBe("boom");
  });

  it("stringifies anything else", () => {
    expect(errorMessage("plain")).toBe("plain");
    expect(errorMessage(42)).toBe("42");
  });
});
