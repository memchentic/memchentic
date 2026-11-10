import { describe, expect, it } from "vitest";
import { formatDocumentTitle } from "./documentTitle";

describe("formatDocumentTitle", () => {
  it("returns the app name when the page title is missing or blank", () => {
    expect(formatDocumentTitle("")).toBe("Memchentic");
    expect(formatDocumentTitle("   ")).toBe("Memchentic");
    expect(formatDocumentTitle(null)).toBe("Memchentic");
    expect(formatDocumentTitle(undefined)).toBe("Memchentic");
  });

  it("joins the app name and the trimmed page title", () => {
    expect(formatDocumentTitle("Groups")).toBe("Memchentic | Groups");
    expect(formatDocumentTitle("  Groups  ")).toBe("Memchentic | Groups");
  });

  it("does not repeat the app name", () => {
    expect(formatDocumentTitle("Memchentic")).toBe("Memchentic");
    expect(formatDocumentTitle("Acme", "Acme")).toBe("Acme");
  });

  it("uses a custom app name and falls back when it is blank", () => {
    expect(formatDocumentTitle("Groups", "Acme")).toBe("Acme | Groups");
    expect(formatDocumentTitle("Groups", "  ")).toBe("Memchentic | Groups");
    expect(formatDocumentTitle("Groups", null)).toBe("Memchentic | Groups");
  });
});
