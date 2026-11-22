import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useReloadOnMemoryEvents } from "./useReloadOnMemoryEvents";

const startMemoryLive = vi.fn();

vi.mock("../liveMemories", async () => {
  const actual = await vi.importActual<typeof import("../liveMemories")>("../liveMemories");
  return {
    ...actual,
    startMemoryLive: (...args: unknown[]) => startMemoryLive(...args),
  };
});

describe("useReloadOnMemoryEvents", () => {
  afterEach(() => {
    vi.useRealTimers();
    startMemoryLive.mockReset();
  });

  it("debounces reload on memory events and skips safety poll", () => {
    vi.useFakeTimers();
    const reload = vi.fn();
    const stop = vi.fn();
    startMemoryLive.mockReturnValue(stop);

    const { unmount } = renderHook(() => useReloadOnMemoryEvents(reload, 400));

    expect(startMemoryLive).toHaveBeenCalledTimes(1);
    const handlers = startMemoryLive.mock.calls[0][0] as {
      onEvent: () => void;
      onSafetyPoll?: () => void;
    };
    expect(handlers.onSafetyPoll).toBeUndefined();

    act(() => {
      handlers.onEvent();
      handlers.onEvent();
    });
    expect(reload).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(400);
    });
    expect(reload).toHaveBeenCalledTimes(1);

    unmount();
    expect(stop).toHaveBeenCalledTimes(1);
  });
});
