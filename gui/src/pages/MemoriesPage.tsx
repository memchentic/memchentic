import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faChevronLeft,
  faChevronRight,
  faMagnifyingGlass,
  faPlus,
  faXmark,
} from "@fortawesome/free-solid-svg-icons";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { ENTRY_TYPES, ErrorNote, MemoryList, PageHeader, SpaceSelect, type MemoryRow } from "../components/ui";

const PAGE = 25;

export default function MemoriesPage() {
  const [params, setParams] = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const [draft, setDraft] = useState(params.get("q") ?? "");
  const [space, setSpace] = useState("");
  const [kind, setKind] = useState("");
  const [offset, setOffset] = useState(0);
  const [rows, setRows] = useState<MemoryRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);
  const searching = q.length > 0;

  const load = useCallback(async () => {
    try {
      if (searching) {
        const qs = new URLSearchParams({ q, kind: kind || "entry" });
        if (space) qs.set("space_id", space);
        setRows(await api<MemoryRow[]>(`/api/memories/search?${qs}`));
      } else {
        const listParams = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
        if (space) listParams.set("space_id", space);
        if (kind) listParams.set("kind", kind);
        setRows(await api<MemoryRow[]>(`/api/memories?${listParams}`));
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [searching, q, space, kind, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setDraft(params.get("q") ?? "");
  }, [params]);

  function submitSearch(e: FormEvent) {
    e.preventDefault();
    const next = draft.trim();
    setOffset(0);
    if (next) setParams({ q: next });
    else setParams({});
  }

  function clearSearch() {
    setDraft("");
    setOffset(0);
    setParams({});
  }

  const rangeStart = searching ? 1 : offset + 1;
  const rangeEnd = searching ? rows.length : offset + rows.length;

  return (
    <div>
      <PageHeader title="Memories">
        {!searching && (
          <button
            className={`btn${showNew ? " btn-active" : ""}`}
            type="button"
            onClick={() => setShowNew((s) => !s)}
          >
            <FontAwesomeIcon icon={showNew ? faXmark : faPlus} />
            {showNew ? "Cancel" : "New"}
          </button>
        )}
      </PageHeader>
      <div className="page-body">
        <form className="toolbar" onSubmit={submitSearch}>
          <div className="toolbar-search">
            <FontAwesomeIcon icon={faMagnifyingGlass} className="toolbar-search-icon" />
            <input
              className="input"
              placeholder="Search memories"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              aria-label="Search memories"
            />
          </div>
          <button className="btn" type="submit">
            Find
          </button>
          {searching && (
            <button className="btn" type="button" onClick={clearSearch}>
              Clear
            </button>
          )}
          <div className="toolbar-divider" aria-hidden="true" />
          <SpaceSelect
            value={space}
            onChange={(v) => {
              setSpace(v);
              setOffset(0);
            }}
          />
          <select
            className="input w-auto"
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              setOffset(0);
            }}
            aria-label="Memory kind"
          >
            <option value="">Entries + guides</option>
            <option value="entry">Entries</option>
            <option value="guide">Guides</option>
          </select>
        </form>

        {searching && (
          <p className="mb-4 text-xs text-faint">
            Showing hybrid matches for “{q}” — full-text and vector similarity fused.
          </p>
        )}

        <ErrorNote error={error} />

        {!searching && showNew && (
          <NewMemory
            defaultSpace={space}
            onCreated={() => {
              setShowNew(false);
              void load();
            }}
            onCancel={() => setShowNew(false)}
          />
        )}

        <MemoryList rows={rows} empty={searching ? "No matches." : "Nothing here yet."} />

        {!searching && (
          <div className="pager">
            <span className="text-xs text-faint">
              {rows.length === 0 ? "No rows" : `${rangeStart}–${rangeEnd}`}
            </span>
            <div className="pager-actions">
              <button
                className="btn"
                type="button"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE))}
              >
                <FontAwesomeIcon icon={faChevronLeft} /> Prev
              </button>
              <button
                className="btn"
                type="button"
                disabled={rows.length < PAGE}
                onClick={() => setOffset(offset + PAGE)}
              >
                Next <FontAwesomeIcon icon={faChevronRight} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function NewMemory({
  defaultSpace,
  onCreated,
  onCancel,
}: {
  defaultSpace: string;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState<"entry" | "guide">("entry");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [type, setType] = useState("note");
  const [tags, setTags] = useState("");
  const [space, setSpace] = useState(defaultSpace);
  const [force, setForce] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const tagList = tags.split(",").map((t) => t.trim()).filter(Boolean);
    const payload = { title, body, tags: tagList, space_id: space || null };
    try {
      if (kind === "guide") {
        await api("/api/guides", { method: "POST", body: JSON.stringify(payload) });
      } else {
        await api("/api/memories", { method: "POST", body: JSON.stringify({ ...payload, type, force }) });
      }
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <form className="panel composer mb-6" onSubmit={submit}>
      <div className="composer-header">
        <h3 className="composer-title">New memory</h3>
        <button className="btn" type="button" onClick={onCancel}>
          <FontAwesomeIcon icon={faXmark} /> Close
        </button>
      </div>
      <ErrorNote error={error} />
      <div className="composer-grid">
        <label className="field">
          <span>Kind</span>
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value as "entry" | "guide")}>
            <option value="entry">Entry</option>
            <option value="guide">Guide</option>
          </select>
        </label>
        {kind === "entry" && (
          <label className="field">
            <span>Type</span>
            <select className="input" value={type} onChange={(e) => setType(e.target.value)}>
              {ENTRY_TYPES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span>Space</span>
          <SpaceSelect value={space} onChange={setSpace} allowAll={false} />
        </label>
        <label className="field field-span">
          <span>Title</span>
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} required />
        </label>
        <label className="field field-span">
          <span>Body</span>
          <textarea className="input min-h-28" value={body} onChange={(e) => setBody(e.target.value)} required />
        </label>
        <label className="field field-span">
          <span>Tags</span>
          <input
            className="input"
            placeholder="comma, separated"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
          />
        </label>
      </div>
      <div className="composer-footer">
        {kind === "entry" && (
          <label className="flex items-center gap-2 text-sm text-muted">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
            Save even if a near-duplicate exists
          </label>
        )}
        <button className="btn btn-primary ml-auto" type="submit">
          Save
        </button>
      </div>
    </form>
  );
}
