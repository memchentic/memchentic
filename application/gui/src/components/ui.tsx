import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faPlus, faXmark } from "@fortawesome/free-solid-svg-icons";
import { useEffect, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { formatDocumentTitle } from "../documentTitle";

export type MemoryRow = {
  id: string;
  space_id?: string;
  space?: string;
  kind: string;
  type: string | null;
  title: string;
  tags: string[];
  snippet?: string;
  body?: string;
  score?: number;
  confidence?: number;
  updated_at?: string | null;
  created_at?: string | null;
  expires_at?: string | null;
  path_hint?: string | null;
  repo_hint?: string | null;
};

export const ENTRY_TYPES = [
  "fact",
  "preference",
  "decision",
  "instruction",
  "feedback",
  "insight",
  "note",
];

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
    >
      <FontAwesomeIcon icon={open ? faXmark : faPlus} />
      {open ? "Cancel" : "New"}
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
  allowAll = true,
}: {
  value: string;
  onChange: (v: string) => void;
  allowAll?: boolean;
}) {
  const { me } = useAuth();
  return (
    <select className="input w-auto" value={value} onChange={(e) => onChange(e.target.value)}>
      {allowAll && <option value="">All my spaces</option>}
      {me?.memberships.map((s) => (
        <option key={s.id} value={s.id}>
          {s.kind === "personal" ? "Personal" : s.name}
        </option>
      ))}
    </select>
  );
}

export function useSpaceName() {
  const { me } = useAuth();
  return (id?: string) => {
    const s = me?.memberships.find((m) => m.id === id);
    if (!s) return "";
    return s.kind === "personal" ? "Personal" : s.name;
  };
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

/** Skip snippets that only repeat the title (common when title is the first sentence). */
function distinctSnippet(title: string, snippet?: string): string | null {
  if (!snippet) return null;
  const norm = (s: string) => s.replace(/\s+/g, " ").replace(/[….\s]+$/g, "").toLowerCase();
  const t = norm(title);
  const sn = norm(snippet);
  if (!sn || sn === t || sn.startsWith(t) || t.startsWith(sn)) return null;
  return snippet;
}

export function MemoryList({ rows, empty }: { rows: MemoryRow[]; empty: string }) {
  const navigate = useNavigate();
  const spaceName = useSpaceName();
  const showScore = rows.some((m) => m.score !== undefined);

  if (!rows.length) {
    return (
      <div className="panel empty-state">
        <p className="text-sm text-faint">{empty}</p>
      </div>
    );
  }

  return (
    <div className="panel overflow-x-auto">
      <table className="data-table memory-table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Type</th>
            <th>Space</th>
            <th>Updated</th>
            {showScore ? <th>Score</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => {
            const kindLabel = m.kind === "guide" ? "guide" : m.type ?? "entry";
            const tags = m.tags.slice(0, 4);
            const extra = m.tags.length - tags.length;
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
                  {tags.length > 0 ? (
                    <div className="memory-tags">
                      {tags.map((t) => (
                        <span key={t} className="tag">
                          {t}
                        </span>
                      ))}
                      {extra > 0 ? <span className="text-xs text-faint">+{extra}</span> : null}
                    </div>
                  ) : null}
                </td>
                <td>
                  <span className={`kind-chip kind-chip-${kindLabel}`}>
                    {kindLabel}
                  </span>
                </td>
                <td className="text-muted">{spaceName(m.space_id ?? m.space) || "—"}</td>
                <td className="text-faint whitespace-nowrap">
                  {formatWhen(m.updated_at ?? m.created_at)}
                </td>
                {showScore ? (
                  <td className="text-faint tabular-nums">
                    {m.score !== undefined ? m.score.toFixed(3) : "—"}
                  </td>
                ) : null}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
