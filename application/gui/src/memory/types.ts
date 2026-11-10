
export type MemoryRow = {
  id: string;
  space_id?: string;
  kind: string;
  type: string | null;
  title: string;
  tags: string[];
  snippet?: string;
  body?: string;
  score?: number;
  confidence?: number;
  created_by?: string | null;
  created_by_email?: string | null;
  created_by_display_name?: string | null;
  updated_at?: string | null;
  created_at?: string | null;
  path_hint?: string | null;
};

export const ENTRY_TYPES = [
  "fact",
  "preference",
  "decision",
  "instruction",
  "feedback",
  "insight",
  "note",
] as const;

export const MEMORY_TYPE_TOKENS = [...ENTRY_TYPES, "guide"] as const;

export type MemoryTypeToken = (typeof MEMORY_TYPE_TOKENS)[number];

export type SpaceScope = "" | "personal" | "group";

/** Empty or every token means the type filter is unrestricted. */
export function isUnrestrictedTypes(selected: readonly MemoryTypeToken[]): boolean {
  return selected.length === 0 || selected.length === MEMORY_TYPE_TOKENS.length;
}
