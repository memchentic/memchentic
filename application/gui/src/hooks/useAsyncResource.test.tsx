import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useAsyncResource } from "./useAsyncResource";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useAsyncResource", () => {
  it("loads on mount and reports loading and loaded", async () => {
    const loader = () => Promise.resolve(7);
    const { result } = renderHook(() => useAsyncResource(loader));
    expect(result.current.loading).toBe(true);
    expect(result.current.loaded).toBe(false);
    await waitFor(() => expect(result.current.loaded).toBe(true));
    expect(result.current.data).toBe(7);
    expect(result.current.error).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("loads nothing for a null loader", () => {
    const { result } = renderHook(() => useAsyncResource<number>(null));
    expect(result.current.loading).toBe(false);
    expect(result.current.data).toBeNull();
  });

  it("keeps the last data on failure unless clearOnError is set", async () => {
    let fail = false;
    const loader = () => (fail ? Promise.reject(new Error("down")) : Promise.resolve("ok"));
    const keep = renderHook(() => useAsyncResource(loader));
    const clear = renderHook(() => useAsyncResource(loader, { clearOnError: true }));
    await waitFor(() => expect(keep.result.current.data).toBe("ok"));
    await waitFor(() => expect(clear.result.current.data).toBe("ok"));
    fail = true;
    await act(() => keep.result.current.reload());
    await act(() => clear.result.current.reload());
    expect(keep.result.current.error).toBe("down");
    expect(keep.result.current.data).toBe("ok");
    expect(clear.result.current.error).toBe("down");
    expect(clear.result.current.data).toBeNull();
  });

  it("discards a response that arrives after a newer request", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const loaderA = () => first.promise;
    const loaderB = () => second.promise;
    const { result, rerender } = renderHook(({ loader }) => useAsyncResource(loader), {
      initialProps: { loader: loaderA },
    });
    rerender({ loader: loaderB });
    expect(result.current.loading).toBe(true);
    await act(async () => {
      second.resolve("new");
      await second.promise;
    });
    await act(async () => {
      first.resolve("old");
      await first.promise;
    });
    expect(result.current.data).toBe("new");
  });

  it("ignores a response after unmount", async () => {
    const pending = deferred<string>();
    const loader = () => pending.promise;
    const { result, unmount } = renderHook(() => useAsyncResource(loader));
    unmount();
    await act(async () => {
      pending.resolve("late");
      await pending.promise;
    });
    expect(result.current.data).toBeNull();
  });
});
