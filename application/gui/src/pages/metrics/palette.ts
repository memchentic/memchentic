import type { CSSProperties } from "react";

/** Axis/tooltip chrome stays greyscale; series use kind-chip hues. */
export const CHROME = {
  ink: "oklch(0.985 0 0)",
  muted: "oklch(0.708 0 0)",
  grid: "oklch(0.269 0 0)",
};

/** Matches `.kind-chip-*` hues in index.css, bumped chroma for chart fills. */
const TYPE_FILL: Record<string, string> = {
  guide: "oklch(0.68 0.12 230)",
  fact: "oklch(0.68 0.12 250)",
  preference: "oklch(0.68 0.13 310)",
  decision: "oklch(0.72 0.14 85)",
  instruction: "oklch(0.68 0.13 165)",
  feedback: "oklch(0.68 0.14 25)",
  insight: "oklch(0.68 0.14 295)",
  note: "oklch(0.66 0.04 260)",
  entry: "oklch(0.7 0.08 250)",
};

export const SERIES = {
  entries: TYPE_FILL.entry,
  guides: TYPE_FILL.guide,
  warn: "oklch(0.72 0.14 55)",
  spaces: "oklch(0.68 0.12 200)",
};

/** Distinct categorical hues for spaces / tags / creators. */
const CATEGORY_FILLS = [
  "oklch(0.68 0.12 230)",
  "oklch(0.72 0.14 85)",
  "oklch(0.68 0.13 165)",
  "oklch(0.68 0.13 310)",
  "oklch(0.68 0.14 25)",
  "oklch(0.68 0.14 295)",
  "oklch(0.68 0.12 250)",
  "oklch(0.7 0.1 145)",
  "oklch(0.7 0.1 40)",
  "oklch(0.66 0.1 200)",
];

export function typeFill(type: string): string {
  return TYPE_FILL[type] ?? TYPE_FILL.note;
}

export function kindFill(kind: string): string {
  if (kind === "guide" || kind === "guides") return SERIES.guides;
  if (kind === "entry" || kind === "entries") return SERIES.entries;
  return typeFill(kind);
}

export function categoryFill(index: number): string {
  return CATEGORY_FILLS[index % CATEGORY_FILLS.length];
}

export function tooltipStyle(): CSSProperties {
  return {
    background: "oklch(0.205 0 0)",
    border: "1px solid oklch(0.269 0 0)",
    borderRadius: 0,
    color: CHROME.ink,
    fontSize: 12,
  };
}
