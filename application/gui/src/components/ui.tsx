import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faChevronDown, faPlus, faTrash, faXmark } from "@fortawesome/free-solid-svg-icons";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { spaceLabel, useAuth, writableSpaces } from "../auth";
import type { Me } from "../auth";
import { formatDocumentTitle } from "../documentTitle";

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

/** Shared overlay dialog with enter/exit motion. */
export function Modal({
  open,
  onClose,
  labelledBy,
  children,
  closeDisabled = false,
}: {
  open: boolean;
  onClose: () => void;
  labelledBy?: string;
  children: ReactNode;
  closeDisabled?: boolean;
}) {
  const { mounted, leaving } = usePresence(open);

  useEffect(() => {
    if (!open) return undefined;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && !closeDisabled) onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose, closeDisabled]);

  if (!mounted) return null;

  return (
    <div
      className={`overlay-backdrop${leaving ? " overlay-backdrop-leave" : ""}`}
      role="dialog"
      aria-modal="true"
      aria-labelledby={labelledBy}
      onClick={(e) => {
        if (e.target === e.currentTarget && !closeDisabled) onClose();
      }}
    >
      <div className="overlay-panel">{children}</div>
    </div>
  );
}

/** Height-reveal shell for New composers so the table does not jump. */
export function ComposerReveal({
  open,
  children,
}: {
  open: boolean;
  children: ReactNode;
}) {
  const { mounted, leaving } = usePresence(open);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (open && mounted && !leaving) {
      const id = requestAnimationFrame(() => {
        requestAnimationFrame(() => setExpanded(true));
      });
      return () => cancelAnimationFrame(id);
    }
    setExpanded(false);
    return undefined;
  }, [open, mounted, leaving]);

  if (!mounted) return null;

  return (
    <div className={`composer-reveal${expanded ? " composer-reveal-open" : ""}`}>
      <div className="composer-reveal-inner">{children}</div>
    </div>
  );
}

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

export function SpaceScopeSelect({
  value,
  onChange,
}: {
  value: SpaceScope;
  onChange: (v: SpaceScope) => void;
}) {
  return (
    <label className="field toolbar-field">
      <span>Space</span>
      <select
        className="input w-auto"
        value={value}
        onChange={(e) => onChange(e.target.value as SpaceScope)}
      >
        <option value="">All</option>
        <option value="group">Groups</option>
        <option value="personal">Personal</option>
      </select>
    </label>
  );
}

export function TypeFilterMenu({
  selected,
  onChange,
}: {
  selected: MemoryTypeToken[];
  onChange: (next: MemoryTypeToken[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const { mounted, leaving } = usePresence(open);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const labelId = useId();
  const allSelected = isUnrestrictedTypes(selected);
  const singleType = !allSelected && selected.length === 1 ? selected[0] : null;
  const label = allSelected
    ? "All"
    : singleType
      ? singleType
      : `${selected.length} types`;

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: globalThis.MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  function toggle(token: MemoryTypeToken) {
    const current = selected.length === 0 ? [...MEMORY_TYPE_TOKENS] : selected;
    if (current.includes(token)) {
      const next = current.filter((t) => t !== token);
      // Empty means unrestricted; snap back to all selected.
      onChange(next.length === 0 ? [...MEMORY_TYPE_TOKENS] : next);
    } else {
      onChange([...current, token]);
    }
  }

  return (
    <div className="field toolbar-field">
      <span id={labelId}>Type</span>
      <div className="filter-menu" ref={rootRef}>
        <button
          aria-controls={menuId}
          aria-expanded={open}
          aria-haspopup="menu"
          aria-labelledby={labelId}
          className="input w-auto filter-menu-trigger"
          onClick={() => setOpen((v) => !v)}
          type="button"
        >
          <span
            className={`filter-menu-label truncate${singleType ? ` type-tint kind-chip-${singleType}` : ""}`}
          >
            {label}
          </span>
          <FontAwesomeIcon
            icon={faChevronDown}
            className={`filter-menu-chevron ${open ? "filter-menu-chevron-open" : ""}`}
          />
        </button>
        {mounted ? (
          <div
            className={`filter-menu-panel${leaving ? " filter-menu-panel-leave" : ""}`}
            id={menuId}
            role="menu"
            aria-labelledby={labelId}
          >
            {MEMORY_TYPE_TOKENS.map((token) => {
              const checked = selected.length === 0 || selected.includes(token);
              return (
                <label
                  key={token}
                  className="filter-menu-item"
                  role="menuitemcheckbox"
                  aria-checked={checked}
                >
                  <input type="checkbox" checked={checked} onChange={() => toggle(token)} />
                  <span className={`type-tint kind-chip-${token}`}>{token}</span>
                </label>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  useEffect(() => {
    document.title = formatDocumentTitle(title);
  }, [title]);

  return (
    <div className="page-header">
      <h2 className="page-header-title">{title}</h2>
      {children ? <div className="page-header-actions">{children}</div> : null}
    </div>
  );
}

/** Header control that opens/closes the shared New composer on list pages. */
export function NewToggleButton({
  open,
  onToggle,
}: {
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      className={`btn${open ? " btn-active" : ""}`}
      type="button"
      onClick={onToggle}
      aria-label={open ? "Cancel" : "New"}
      title={open ? "Cancel" : "New"}
    >
      <FontAwesomeIcon icon={open ? faXmark : faPlus} />
    </button>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <p className="flash-error mb-4" role="alert">
      {error}
    </p>
  );
}

export function SpaceSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const { me } = useAuth();
  const options = writableSpaces(me);
  // `value` is not one of the options in two cases, and each gets one extra
  // option that carries `value` itself, so the label and the request agree.
  // "": `NewMemory.submit` sends `space_id: null` and the server writes to
  // the caller's personal space, so the option is labelled "Personal".
  // Any other value is a space the caller can no longer write to: it is sent
  // unchanged and the server refuses it, so the option says so.
  const known = options.some((s) => s.id === value);
  return (
    <select className="input w-auto" value={value} onChange={(e) => onChange(e.target.value)}>
      {known ? null : (
        <option value={value}>{value ? "Unavailable space" : "Personal"}</option>
      )}
      {options.map((s) => (
        <option key={s.id} value={s.id}>
          {s.kind === "personal" ? "Personal" : s.name}
        </option>
      ))}
    </select>
  );
}

export function useSpaceName() {
  const { me } = useAuth();
  return (id?: string) => spaceLabel(me, id);
}

function formatWhen(value?: string | null): string {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

const TAG_PREVIEW = 6;

/** Skip snippets that only repeat the title (common when title is the first sentence). */
function distinctSnippet(title: string, snippet?: string): string | null {
  if (!snippet) return null;
  const norm = (s: string) => s.replace(/\s+/g, " ").replace(/[….\s]+$/g, "").toLowerCase();
  const t = norm(title);
  const sn = norm(snippet);
  if (!sn || sn === t || sn.startsWith(t) || t.startsWith(sn)) return null;
  return snippet;
}

export function MemoryList({
  rows,
  empty,
  onDeleted,
}: {
  rows: MemoryRow[];
  empty: string;
  onDeleted?: (id: string) => void;
}) {
  const { me } = useAuth();
  const navigate = useNavigate();
  const spaceName = useSpaceName();
  const showScore = rows.some((m) => m.score !== undefined);
  const [error, setError] = useState<string | null>(null);
  const [forgettingId, setForgettingId] = useState<string | null>(null);

  async function forgetRow(e: MouseEvent, id: string) {
    e.stopPropagation();
    if (!confirm("Forget this memory permanently?")) return;
    setForgettingId(id);
    setError(null);
    try {
      await api(`/api/memories/${id}`, { method: "DELETE" });
      onDeleted?.(id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setForgettingId(null);
    }
  }

  if (!rows.length) {
    return (
      <div className="panel empty-state">
        <p className="text-sm text-faint">{empty}</p>
      </div>
    );
  }

  return (
    <div className="panel overflow-x-auto">
      <ErrorNote error={error} />
      <table className="data-table memory-table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Type</th>
            <th>Tags</th>
            <th>Space</th>
            <th>Owner</th>
            <th>Updated</th>
            {showScore ? <th>Score</th> : null}
            <th className="memory-actions-col">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => {
            const kindLabel = m.kind === "guide" ? "guide" : m.type ?? "entry";
            const visibleTags = m.tags.slice(0, TAG_PREVIEW);
            const hiddenCount = m.tags.length - visibleTags.length;
            const snippet = distinctSnippet(m.title, m.snippet);
            return (
              <tr
                key={m.id}
                className="cursor-pointer"
                onClick={() => navigate(`/entries/${m.id}`)}
              >
                <td>
                  <Link
                    to={`/entries/${m.id}`}
                    className="memory-title-link"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {m.title}
                  </Link>
                  {snippet ? <p className="memory-snippet">{snippet}</p> : null}
                </td>
                <td>
                  <span className={`kind-chip kind-chip-${kindLabel}`}>
                    {kindLabel}
                  </span>
                </td>
                <td>
                  {m.tags.length > 0 ? (
                    <div className="memory-tags">
                      {visibleTags.map((t) => (
                        <span key={t} className="tag">
                          {t}
                        </span>
                      ))}
                      {hiddenCount > 0 ? (
                        <span className="memory-tags-more" title={`${hiddenCount} more tags`}>
                          +{hiddenCount}
                        </span>
                      ) : null}
                    </div>
                  ) : (
                    <span className="text-faint">—</span>
                  )}
                </td>
                <td className="text-muted">{spaceName(m.space_id) || "—"}</td>
                <td className="text-muted whitespace-nowrap">{formatMemoryOwner(m, me)}</td>
                <td className="text-faint whitespace-nowrap">
                  {formatWhen(m.updated_at ?? m.created_at)}
                </td>
                {showScore ? (
                  <td className="text-faint tabular-nums">
                    {m.score !== undefined ? m.score.toFixed(3) : "—"}
                  </td>
                ) : null}
                <td className="memory-actions-col">
                  {canForgetMemory(m, me) ? (
                    <button
                      className="btn memory-row-forget"
                      type="button"
                      title="Forget"
                      aria-label={`Forget ${m.title}`}
                      disabled={forgettingId === m.id}
                      onClick={(e) => void forgetRow(e, m.id)}
                    >
                      <FontAwesomeIcon icon={faTrash} />
                    </button>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
