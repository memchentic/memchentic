import { useEffect, useState } from "react";

/** Matches `--duration-exit` in index.css. */
const MOTION_EXIT_MS = 120;

/**
 * Keep a node mounted through its CSS exit transition.
 * `leaving` is true while the leave class should be applied.
 */
export function usePresence(open: boolean, exitMs = MOTION_EXIT_MS) {
  const [phase, setPhase] = useState<"out" | "in" | "leaving">(open ? "in" : "out");

  useEffect(() => {
    if (open) {
      setPhase("in");
      return;
    }
    setPhase((prev) => (prev === "out" ? "out" : "leaving"));
  }, [open]);

  useEffect(() => {
    if (phase !== "leaving") return undefined;
    const id = window.setTimeout(() => setPhase("out"), exitMs);
    return () => window.clearTimeout(id);
  }, [phase, exitMs]);

  return { mounted: phase !== "out", leaving: phase === "leaving" };
}
