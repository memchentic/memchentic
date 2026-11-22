import {
  MEMORY_TYPE_TOKENS,
  type MemoryTypeToken,
  type SpaceScope,
} from "../../memory/types";

const TYPE_SET = new Set<string>(MEMORY_TYPE_TOKENS);

export type ParsedMetricsQuery = {
  q: string;
  kind: "" | "entry" | "guide";
  types: MemoryTypeToken[];
  tags: string[];
  spaceScope: SpaceScope;
};

/**
 * Parse a Datadog-ish query strip into facet fields.
 *
 * Recognized tokens: ``kind:entry|guide``, ``type:<entry-type|guide>``,
 * ``tag:<name>``, ``space:personal|group|all``. Remaining words form ``q``.
 */
export function parseMetricsQuery(raw: string): ParsedMetricsQuery {
  const tokens = raw.trim().split(/\s+/).filter(Boolean);
  const free: string[] = [];
  let kind: "" | "entry" | "guide" = "";
  const types: MemoryTypeToken[] = [];
  const tags: string[] = [];
  let spaceScope: SpaceScope = "";

  for (const token of tokens) {
    const colon = token.indexOf(":");
    if (colon <= 0) {
      free.push(token);
      continue;
    }
    const key = token.slice(0, colon).toLowerCase();
    const value = token.slice(colon + 1).trim();
    if (!value) {
      free.push(token);
      continue;
    }
    if (key === "kind" && (value === "entry" || value === "guide")) {
      kind = value;
      continue;
    }
    if (key === "type" && TYPE_SET.has(value)) {
      types.push(value as MemoryTypeToken);
      continue;
    }
    if (key === "tag") {
      tags.push(value);
      continue;
    }
    if (key === "space") {
      if (value === "personal" || value === "group") spaceScope = value;
      else if (value === "all") spaceScope = "";
      continue;
    }
    free.push(token);
  }

  return { q: free.join(" "), kind, types, tags, spaceScope };
}

/** Build the query strip from structured facets (for chip removal / sync). */
export function formatMetricsQuery(parts: {
  q: string;
  kind: "" | "entry" | "guide";
  types: string[];
  tags: string[];
  spaceScope: SpaceScope;
}): string {
  const tokens: string[] = [];
  if (parts.kind) tokens.push(`kind:${parts.kind}`);
  for (const t of parts.types) tokens.push(`type:${t}`);
  for (const tag of parts.tags) tokens.push(`tag:${tag}`);
  if (parts.spaceScope) tokens.push(`space:${parts.spaceScope}`);
  if (parts.q.trim()) tokens.push(parts.q.trim());
  return tokens.join(" ");
}
