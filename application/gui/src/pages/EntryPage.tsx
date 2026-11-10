import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faFloppyDisk, faPen, faTrash, faXmark } from "@fortawesome/free-solid-svg-icons";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import {
  ENTRY_TYPES,
  ErrorNote,
  PageHeader,
  canForgetMemory,
  useSpaceName,
  type MemoryRow,
} from "../components/ui";
import { formatDateTime } from "../format";
import { errorMessage } from "../errors";

/** Edit-form values for a memory row. */
function draftFromRow(row: MemoryRow) {
  return {
    title: row.title,
    body: row.body ?? "",
    type: row.type ?? "note",
    tags: row.tags.join(", "),
  };
}

export default function EntryPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const spaceName = useSpaceName();
  const { me } = useAuth();
  const [m, setM] = useState<MemoryRow | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ title: "", body: "", type: "note", tags: "" });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<MemoryRow>(`/api/memories/${id}`)
      .then((row) => {
        setM(row);
        setDraft(draftFromRow(row));
      })
      .catch((err) => setError(errorMessage(err)));
  }, [id]);

  const [saving, setSaving] = useState(false);

  async function save() {
    if (saving) return;
    setSaving(true);
    try {
      const row = await api<MemoryRow>(`/api/memories/${id}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: draft.title,
          body: draft.body,
          type: m?.kind === "entry" ? draft.type : undefined,
          tags: draft.tags.split(",").map((t) => t.trim()).filter(Boolean),
        }),
      });
      setM(row);
      setEditing(false);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function forget() {
    if (!confirm("Forget this entry permanently?")) return;
    try {
      await api(`/api/memories/${id}`, { method: "DELETE" });
      navigate("/");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  if (!m && !error) {
    return <p className="text-sm text-muted">Loading…</p>;
  }
  if (!m) {
    return (
      <div className="page-body">
        <ErrorNote error={error} />
      </div>
    );
  }

  return (
    <div>
      <PageHeader title={editing ? "Revise" : m.title}>
        {editing ? (
          <>
            <button
              className="btn"
              type="button"
              title="Save"
              aria-label="Save"
              disabled={saving}
              onClick={() => void save()}
            >
              <FontAwesomeIcon icon={faFloppyDisk} />
            </button>
            <button
              className="btn"
              type="button"
              title="Cancel"
              aria-label="Cancel"
              onClick={() => setEditing(false)}
            >
              <FontAwesomeIcon icon={faXmark} />
            </button>
          </>
        ) : (
          <>
            {canForgetMemory(m, me) ? (
              <button
                className="btn"
                type="button"
                title="Revise"
                aria-label="Revise"
                onClick={() => {
                  // Start every edit from the saved memory, not from an abandoned draft.
                  setDraft(draftFromRow(m));
                  setEditing(true);
                }}
              >
                <FontAwesomeIcon icon={faPen} />
              </button>
            ) : null}
            {canForgetMemory(m, me) ? (
              <button
                className="btn"
                type="button"
                title="Forget"
                aria-label="Forget"
                onClick={() => void forget()}
              >
                <FontAwesomeIcon icon={faTrash} />
              </button>
            ) : null}
          </>
        )}
      </PageHeader>
      <div className="page-body">
        <ErrorNote error={error} />
        <p className="mb-4 text-xs text-faint">
          {m.kind === "guide" ? `guide · confidence ${m.confidence}` : m.type} · {spaceName(m.space_id) || "—"} · updated{" "}
          {formatDateTime(m.updated_at)}
          {m.path_hint && ` · ${m.path_hint}`}
        </p>
        {editing ? (
          <div key="revise" className="panel reveal-fade grid gap-3 p-4">
            <input className="input" aria-label="Title" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
            {m.kind === "entry" && (
              <select className="input w-auto" aria-label="Type" value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value })}>
                {ENTRY_TYPES.map((t) => <option key={t}>{t}</option>)}
              </select>
            )}
            <textarea className="input min-h-64" aria-label="Body" value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
            <input className="input" aria-label="Tags, comma-separated" value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} />
          </div>
        ) : (
          <div key="view" className="panel reveal-fade p-4">
            <pre className="whitespace-pre-wrap font-sans text-sm text-ink">{m.body}</pre>
            {m.tags.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-1">
                {m.tags.map((t) => (
                  <span key={t} className="tag">
                    {t}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
