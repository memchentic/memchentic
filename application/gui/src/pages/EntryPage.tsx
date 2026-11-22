import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faBan,
  faFloppyDisk,
  faPen,
  faThumbsDown,
  faThumbsUp,
  faTrash,
  faXmark,
} from "@fortawesome/free-solid-svg-icons";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ENTRY_TYPES, type MemoryRow } from "../memory/types";
import { ErrorNote, PageHeader } from "../components/page";
import { canForgetMemory, formatMemorySpace } from "../memory/rowFormat";
import { formatDateTime } from "../format";
import { errorMessage } from "../errors";

type HistoryRow = {
  id: string;
  revised_at: string | null;
  source: string;
  changed_fields: string[];
  revised_by: string | null;
};

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
  const { me } = useAuth();
  const [m, setM] = useState<MemoryRow | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ title: "", body: "", type: "note", tags: "" });
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [feedbackNote, setFeedbackNote] = useState<string | null>(null);

  useEffect(() => {
    api<MemoryRow>(`/api/memories/${id}`)
      .then((row) => {
        setM(row);
        setDraft(draftFromRow(row));
      })
      .catch((err) => setError(errorMessage(err)));
    api<HistoryRow[]>(`/api/memories/${id}/history?limit=20`)
      .then(setHistory)
      .catch(() => setHistory([]));
  }, [id]);

  const [saving, setSaving] = useState(false);

  async function sendFeedback(signal: "useful" | "stale" | "wrong") {
    try {
      await api(`/api/memories/${id}/feedback`, {
        method: "POST",
        body: JSON.stringify({ signal }),
      });
      setFeedbackNote(
        signal === "useful" ? "Marked useful." : signal === "stale" ? "Marked stale." : "Marked wrong.",
      );
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

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
          {m.kind === "guide" ? `guide · confidence ${m.confidence}` : m.type} ·{" "}
          {formatMemorySpace(m, me) || "—"} · updated{" "}
          {formatDateTime(m.updated_at)}
          {m.path_hint && ` · ${m.path_hint}`}
        </p>
        {!editing ? (
          <section className="mb-6" aria-label="Memory metrics">
            <h2 className="metrics-section-title">Usage</h2>
            <div className="metrics-kpi-grid">
              <div className="metrics-kpi panel">
                <div className="metrics-kpi-label">Recalls</div>
                <div className="metrics-kpi-value">{m.recall_count ?? 0}</div>
              </div>
              <div className="metrics-kpi panel">
                <div className="metrics-kpi-label">Last recalled</div>
                <div className="metrics-kpi-value metrics-kpi-value-sm">
                  {m.last_recalled_at ? formatDateTime(m.last_recalled_at) : "Never"}
                </div>
              </div>
              <div className="metrics-kpi panel">
                <div className="metrics-kpi-label">Created</div>
                <div className="metrics-kpi-value metrics-kpi-value-sm">
                  {formatDateTime(m.created_at)}
                </div>
              </div>
              <div className="metrics-kpi panel">
                <div className="metrics-kpi-label">Expires</div>
                <div className="metrics-kpi-value metrics-kpi-value-sm">
                  {m.expires_at ? formatDateTime(m.expires_at) : "Permanent"}
                </div>
              </div>
            </div>
            {(m.recent_recalls?.length ?? 0) > 0 ? (
              <div className="panel p-4">
                <h3 className="metrics-chart-title">Recent accesses</h3>
                <ul className="entry-recall-list">
                  {(m.recent_recalls ?? []).map((event, index) => {
                    const who =
                      event.recalled_by_display_name?.trim() ||
                      event.recalled_by_email ||
                      (event.recalled_by ? event.recalled_by.slice(0, 8) : "—");
                    return (
                      <li key={`${event.recalled_at ?? "x"}-${index}`}>
                        <span className="entry-recall-when">
                          {event.recalled_at ? formatDateTime(event.recalled_at) : "—"}
                        </span>
                        <span className="entry-recall-source">{event.source}</span>
                        <span className="entry-recall-who">{who}</span>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ) : (
              <p className="text-sm text-muted">
                No recorded access events yet. Lifetime recalls still count
                touches from before event logging began.
              </p>
            )}
            <div className="mt-4 flex flex-wrap gap-2" role="group" aria-label="Feedback">
              <button
                className="btn"
                type="button"
                title="Useful"
                aria-label="Useful"
                onClick={() => void sendFeedback("useful")}
              >
                <FontAwesomeIcon icon={faThumbsUp} />
              </button>
              <button
                className="btn"
                type="button"
                title="Stale"
                aria-label="Stale"
                onClick={() => void sendFeedback("stale")}
              >
                <FontAwesomeIcon icon={faThumbsDown} />
              </button>
              <button
                className="btn"
                type="button"
                title="Wrong"
                aria-label="Wrong"
                onClick={() => void sendFeedback("wrong")}
              >
                <FontAwesomeIcon icon={faBan} />
              </button>
              {feedbackNote ? <span className="text-xs text-muted self-center">{feedbackNote}</span> : null}
            </div>
            <div className="panel p-4 mt-4">
              <h3 className="metrics-chart-title">Edit history</h3>
              {history.length === 0 ? (
                <p className="text-sm text-muted">No revisions yet.</p>
              ) : (
                <ul className="entry-recall-list">
                  {history.map((rev) => (
                    <li key={rev.id}>
                      <span className="entry-recall-when">
                        {rev.revised_at ? formatDateTime(rev.revised_at) : "—"}
                      </span>
                      <span className="entry-recall-source">{rev.source}</span>
                      <span className="entry-recall-who">
                        {(rev.changed_fields || []).join(", ") || "—"}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        ) : null}
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
