import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faFloppyDisk, faPen, faTrash, faXmark } from "@fortawesome/free-solid-svg-icons";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { ENTRY_TYPES, ErrorNote, PageHeader, useSpaceName, type MemoryRow } from "../components/ui";

export default function EntryPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const spaceName = useSpaceName();
  const [m, setM] = useState<MemoryRow | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ title: "", body: "", type: "note", tags: "" });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<MemoryRow>(`/api/memories/${id}`)
      .then((row) => {
        setM(row);
        setDraft({ title: row.title, body: row.body ?? "", type: row.type ?? "note", tags: row.tags.join(", ") });
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [id]);

  async function save() {
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
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function forget() {
    if (!confirm("Forget this entry permanently?")) return;
    try {
      await api(`/api/memories/${id}`, { method: "DELETE" });
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (!m) return <ErrorNote error={error ?? "Loading…"} />;

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
            <button
              className="btn"
              type="button"
              title="Revise"
              aria-label="Revise"
              onClick={() => setEditing(true)}
            >
              <FontAwesomeIcon icon={faPen} />
            </button>
            <button
              className="btn"
              type="button"
              title="Forget"
              aria-label="Forget"
              onClick={() => void forget()}
            >
              <FontAwesomeIcon icon={faTrash} />
            </button>
          </>
        )}
      </PageHeader>
      <div className="page-body">
        <ErrorNote error={error} />
        <p className="mb-4 text-xs text-faint">
          {m.kind === "guide" ? `guide · confidence ${m.confidence}` : m.type} · {spaceName(m.space_id)} · updated{" "}
          {m.updated_at ? new Date(m.updated_at).toLocaleString() : "—"}
          {m.path_hint && ` · ${m.path_hint}`}
        </p>
        {editing ? (
          <div className="panel grid gap-3 p-4">
            <input className="input" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
            {m.kind === "entry" && (
              <select className="input w-auto" value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value })}>
                {ENTRY_TYPES.map((t) => <option key={t}>{t}</option>)}
              </select>
            )}
            <textarea className="input min-h-64" value={draft.body} onChange={(e) => setDraft({ ...draft, body: e.target.value })} />
            <input className="input" value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} />
          </div>
        ) : (
          <div className="panel p-4">
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
