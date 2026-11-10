import type { Me } from "../auth";
import type { MemoryRow } from "./types";

/** Prefer display name, then email, then a short id. */
export function formatMemoryOwner(
  row: MemoryRow,
  me?: { id?: string | null; email?: string | null; display_name?: string | null } | null,
): string {
  const named = row.created_by_display_name?.trim();
  if (named) return named;
  if (row.created_by_email) return row.created_by_email;
  if (row.created_by && me?.id && row.created_by === me.id) {
    return me.display_name?.trim() || me.email || row.created_by.slice(0, 8);
  }
  if (row.created_by) return row.created_by.slice(0, 8);
  return "—";
}

/** Skip snippets that only repeat the title (common when title is the first sentence). */
export function distinctSnippet(title: string, snippet?: string): string | null {
  if (!snippet) return null;
  const norm = (s: string) => s.replace(/\s+/g, " ").replace(/[….\s]+$/g, "").toLowerCase();
  const t = norm(title);
  const sn = norm(snippet);
  if (!sn || sn === t || sn.startsWith(t) || t.startsWith(sn)) return null;
  return snippet;
}

/**
 * Whether to offer Forget (and, on the entry page, Revise) for a memory.
 * Mirrors the server rule (`require_memory_manage`): administrators, the
 * memory's creator, and the creator of the memory's space may manage it.
 */
export function canForgetMemory(
  row: Pick<MemoryRow, "space_id" | "created_by">,
  me: Me | null,
): boolean {
  if (!me) return false;
  if (me.is_admin) return true;
  if (row.created_by && row.created_by === me.id) return true;
  const space = me.memberships.find((s) => s.id === row.space_id);
  // Unknown space, or a server that does not report `created_by` yet: keep
  // the button and let the server decide.
  if (!space || space.created_by === undefined) return true;
  return space.created_by !== null && space.created_by === me.id;
}
