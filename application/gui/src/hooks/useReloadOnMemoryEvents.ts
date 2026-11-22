import { useEffect, useRef } from "react";
import { createDebounced, startMemoryLive } from "../liveMemories";

const DEFAULT_DEBOUNCE_MS = 400;

/**
 * Refetch when a memory create/update/delete arrives over SSE.
 * No safety poll — surfaces that need one should call `startMemoryLive` directly.
 */
export function useReloadOnMemoryEvents(
  reload: () => void | Promise<void>,
  debounceMs: number = DEFAULT_DEBOUNCE_MS,
): void {
  const reloadRef = useRef(reload);
  reloadRef.current = reload;

  useEffect(() => {
    const debounced = createDebounced(() => {
      void reloadRef.current();
    }, debounceMs);
    const stop = startMemoryLive({
      onEvent: () => debounced.trigger(),
    });
    return () => {
      debounced.cancel();
      stop();
    };
  }, [debounceMs]);
}
