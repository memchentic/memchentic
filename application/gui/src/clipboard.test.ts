import { afterEach, describe, expect, it, vi } from "vitest";
import { copyText } from "./clipboard";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("copyText", () => {
  it("returns false without a clipboard", async () => {
    // A non-secure origin: `navigator.clipboard` is undefined.
    vi.stubGlobal("navigator", {});
    expect(await copyText("k")).toBe(false);
  });

  it("returns false when the write rejects", async () => {
    const denied = { writeText: () => Promise.reject(new Error("denied")) };
    expect(await copyText("k", denied)).toBe(false);
  });

  it("returns true and writes the text", async () => {
    const written: string[] = [];
    const fake = {
      writeText: (text: string) => {
        written.push(text);
        return Promise.resolve();
      },
    };
    expect(await copyText("k", fake)).toBe(true);
    expect(written).toEqual(["k"]);
  });

  it("uses the fallback when clipboard is missing", async () => {
    vi.stubGlobal("navigator", {});
    const fallback = vi.fn(() => true);
    expect(await copyText("k", undefined, fallback)).toBe(true);
    expect(fallback).toHaveBeenCalledTimes(1);
  });

  it("uses the fallback when writeText rejects", async () => {
    const denied = { writeText: () => Promise.reject(new Error("denied")) };
    const fallback = vi.fn(() => true);
    expect(await copyText("k", denied, fallback)).toBe(true);
    expect(fallback).toHaveBeenCalledTimes(1);
  });

  it("does not call the fallback when writeText succeeds", async () => {
    const fake = { writeText: () => Promise.resolve() };
    const fallback = vi.fn(() => true);
    expect(await copyText("k", fake, fallback)).toBe(true);
    expect(fallback).not.toHaveBeenCalled();
  });

  it("returns false when the fallback throws", async () => {
    vi.stubGlobal("navigator", {});
    expect(
      await copyText("k", undefined, () => {
        throw new Error("exec failed");
      }),
    ).toBe(false);
  });
});
