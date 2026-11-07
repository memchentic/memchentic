import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";

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
  return (
    <div className="page-header">
      <h2 className="page-header-title">{title}</h2>
      {children ? <div className="page-header-actions">{children}</div> : null}
    </div>
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

export function MemoryList({ rows, empty }: { rows: MemoryRow[]; empty: string }) {
  const spaceName = useSpaceName();
  if (!rows.length) return <p className="text-sm text-faint">{empty}</p>;
  return (
    <ul className="list-stack">
      {rows.map((m) => (
        <li key={m.id}>
          <Link to={`/entries/${m.id}`} className="list-row">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="font-medium text-ink">{m.title}</span>
              <span className="text-xs text-faint">
                {m.kind === "guide" ? "guide" : m.type}
                {" · "}
                {spaceName(m.space_id ?? m.space)}
                {m.score !== undefined && ` · ${m.score.toFixed(4)}`}
              </span>
            </div>
            {m.snippet && <p className="mt-1 text-sm text-muted">{m.snippet}</p>}
            {m.tags.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {m.tags.map((t) => (
                  <span key={t} className="tag">
                    {t}
                  </span>
                ))}
              </div>
            )}
          </Link>
        </li>
      ))}
    </ul>
  );
}
