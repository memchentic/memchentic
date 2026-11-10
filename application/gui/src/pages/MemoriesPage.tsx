import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faChevronLeft,
  faChevronRight,
  faFloppyDisk,
  faMagnifyingGlass,
  faXmark,
} from "@fortawesome/free-solid-svg-icons";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import {
  ComposerReveal,
  ENTRY_TYPES,
  ErrorNote,
  MEMORY_TYPE_TOKENS,
  MemoryList,
  NewToggleButton,
  PageHeader,
  SpaceScopeSelect,
  SpaceSelect,
  TypeFilterMenu,
  isUnrestrictedTypes,
  type MemoryRow,
  type MemoryTypeToken,
  type SpaceScope,
} from "../components/ui";
import { applyMemoryEvent, startMemoryLive } from "../liveMemories";
import { errorMessage } from "../errors";

const PAGE = 25;

function typesQuery(selected: MemoryTypeToken[]): string | null {
  if (isUnrestrictedTypes(selected)) return null;
  return selected.join(",");
}

export default function MemoriesPage() {
  const { me } = useAuth();
  const [params, setParams] = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const [draft, setDraft] = useState(params.get("q") ?? "");
  const [spaceScope, setSpaceScope] = useState<SpaceScope>("");
  const [types, setTypes] = useState<MemoryTypeToken[]>([...MEMORY_TYPE_TOKENS]);
  // The offset belongs to one search query. When `q` changes by any route
  // (search form, Back, Forward, an edited URL) paging restarts at 0.
  const [page, setPage] = useState({ q, offset: 0 });
  const offset = page.q === q ? page.offset : 0;
  const setOffset = (next: number) => setPage({ q, offset: next });
  const [rows, setRows] = useState<MemoryRow[]>([]);
  // True when the last server response filled the page, so a next page may exist.
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // False until the first load attempt has finished.
  const [loaded, setLoaded] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const searching = q.length > 0;
  const loadRef = useRef<() => Promise<void>>(async () => {});
  const loadSeq = useRef(0);
  const rowsRef = useRef<MemoryRow[]>([]);
  rowsRef.current = rows;
  const viewRef = useRef({
    searching,
    spaceScope,
    types,
    offset,
    memberships: me?.memberships ?? [],
    meId: me?.id ?? null,
  });

  const load = useCallback(async () => {
    // Only the newest request may write state; an older, slower response is dropped.
    const seq = ++loadSeq.current;
    const typeFilter = typesQuery(types);
    let path: string;
    if (searching) {
      const qs = new URLSearchParams({
        q,
        limit: String(PAGE),
        offset: String(offset),
      });
      if (spaceScope) qs.set("space_scope", spaceScope);
      qs.set("types", typeFilter ?? MEMORY_TYPE_TOKENS.join(","));
      path = `/api/memories/search?${qs}`;
    } else {
      const listParams = new URLSearchParams({
        limit: String(PAGE),
        offset: String(offset),
      });
      if (spaceScope) listParams.set("space_scope", spaceScope);
      if (typeFilter) listParams.set("types", typeFilter);
      path = `/api/memories?${listParams}`;
    }
    try {
      const next = await api<MemoryRow[]>(path);
      if (seq !== loadSeq.current) return;
      setRows(next);
      setHasMore(next.length >= PAGE);
      setError(null);
    } catch (err) {
      if (seq !== loadSeq.current) return;
      setError(errorMessage(err));
    } finally {
      if (seq === loadSeq.current) setLoaded(true);
    }
  }, [searching, q, spaceScope, types, offset]);

  loadRef.current = load;
  viewRef.current = {
    searching,
    spaceScope,
    types,
    offset,
    memberships: me?.memberships ?? [],
    meId: me?.id ?? null,
  };

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    return startMemoryLive({
      onEvent: (event) => {
        const view = viewRef.current;
        if (view.searching) {
          // Search results are a ranked snapshot. Re-running the search costs
          // an embedding and counts as a recall of every hit, so it happens
          // only when the user searches, filters or pages.
          if (event.action === "deleted") {
            setRows((prev) => prev.filter((row) => row.id !== event.id));
          }
          return;
        }
        if (event.action === "deleted" && rowsRef.current.some((row) => row.id === event.id)) {
          // The page lost a row: reload so it refills from the server.
          void loadRef.current();
          return;
        }
        if (
          event.action !== "deleted" &&
          view.spaceScope !== "" &&
          !view.memberships.some((s) => s.id === event.space_id)
        ) {
          // An administrator receives events for every space. The event does
          // not say whether a space outside `memberships` is personal or a
          // group, so the server decides whether the row is in this view.
          void loadRef.current();
          return;
        }
        if (
          event.action === "created" &&
          view.offset === 0 &&
          event.created_by &&
          event.created_by !== view.meId
        ) {
          // Live events carry no creator name. Reload so the Owner column
          // shows the name instead of an id prefix.
          void loadRef.current();
          return;
        }
        setRows((prev) =>
          applyMemoryEvent(prev, event, {
            spaceScope: view.spaceScope,
            types: view.types,
            memberships: view.memberships,
            offset: view.offset,
            pageSize: PAGE,
          }),
        );
      },
      onSafetyPoll: () => {
        if (!viewRef.current.searching) void loadRef.current();
      },
    });
  }, []);

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

  const rangeStart = offset + 1;
  const rangeEnd = offset + rows.length;
  // A locally shortened page (a row was just forgotten) keeps its next page.
  const canNext = hasMore || rows.length >= PAGE;

  // Only the caller's own personal space is a default write target.
  const ownPersonal = me?.personal_space_id ?? "";
  const defaultWriteSpace =
    spaceScope === "group"
      ? (me?.memberships.find((s) => s.kind === "group")?.id ?? ownPersonal)
      : ownPersonal;

  return (
    <div>
      <PageHeader title="Memories">
        {!searching && (
          <NewToggleButton open={showNew} onToggle={() => setShowNew((s) => !s)} />
        )}
      </PageHeader>
      <div className="page-body">
        <form className="toolbar" onSubmit={submitSearch}>
          <div className="toolbar-search">
            <input
              className="input"
              placeholder="Search memories"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              aria-label="Search memories"
            />
          </div>
          <button className="btn" type="submit" aria-label="Find" title="Find">
            <FontAwesomeIcon icon={faMagnifyingGlass} />
          </button>
          {searching && (
            <button
              className="btn"
              type="button"
              title="Clear"
              aria-label="Clear"
              onClick={clearSearch}
            >
              <FontAwesomeIcon icon={faXmark} />
            </button>
          )}
          <div className="toolbar-divider" aria-hidden="true" />
          <SpaceScopeSelect
            value={spaceScope}
            onChange={(v) => {
              setSpaceScope(v);
              setOffset(0);
            }}
          />
          <TypeFilterMenu
            selected={types}
            onChange={(next) => {
              setTypes(next);
              setOffset(0);
            }}
          />
        </form>

        {searching && (
          <p className="mb-4 text-xs text-faint">
            Showing hybrid matches for “{q}” — full-text and vector similarity fused.
          </p>
        )}

        <ErrorNote error={error} />

        <ComposerReveal open={!searching && showNew}>
          <NewMemory
            defaultSpace={defaultWriteSpace}
            onCreated={() => {
              setShowNew(false);
              void load();
            }}
            onCancel={() => setShowNew(false)}
          />
        </ComposerReveal>

        <MemoryList
          rows={rows}
          empty={!loaded ? "Loading…" : searching ? "No matches." : "Nothing here yet."}
          onDeleted={(id) => {
            setRows((prev) => prev.filter((row) => row.id !== id));
            // A browse page refills from the server. Search results are a
            // ranked snapshot and are not re-run.
            if (!searching) void load();
          }}
        />

        <div className="pager">
          <span className="text-xs text-faint">
            {rows.length === 0
              ? !loaded
                ? "Loading…"
                : searching
                  ? "No matches"
                : "No rows"
              : `${rangeStart}–${rangeEnd}${searching && canNext ? "+" : ""}`}
          </span>
          <div className="pager-actions">
            <button
              className="btn"
              type="button"
              title="Previous"
              aria-label="Previous"
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - PAGE))}
            >
              <FontAwesomeIcon icon={faChevronLeft} />
            </button>
            <button
              className="btn"
              type="button"
              title="Next"
              aria-label="Next"
              disabled={!canNext}
              onClick={() => setOffset(offset + PAGE)}
            >
              <FontAwesomeIcon icon={faChevronRight} />
            </button>
          </div>
        </div>
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

  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const tagList = tags.split(",").map((t) => t.trim()).filter(Boolean);
    const payload = { title, body, tags: tagList, space_id: space || null };
    setBusy(true);
    try {
      if (kind === "guide") {
        await api("/api/guides", { method: "POST", body: JSON.stringify(payload) });
      } else {
        await api("/api/memories", {
          method: "POST",
          body: JSON.stringify({ ...payload, type, force }),
        });
      }
      onCreated();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel composer mb-6" onSubmit={submit}>
      <div className="composer-header">
        <h3 className="composer-title">New memory</h3>
        <button
          className="btn"
          type="button"
          title="Close"
          aria-label="Close"
          onClick={onCancel}
        >
          <FontAwesomeIcon icon={faXmark} />
        </button>
      </div>
      <ErrorNote error={error} />
      <div className="composer-grid">
        <label className="field">
          <span>Kind</span>
          <select
            className="input"
            value={kind}
            onChange={(e) => setKind(e.target.value as "entry" | "guide")}
          >
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
          <SpaceSelect value={space} onChange={setSpace} />
        </label>
        <label className="field field-span">
          <span>Title</span>
          <input
            className="input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
          />
        </label>
        <label className="field field-span">
          <span>Body</span>
          <textarea
            className="input min-h-28"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            required
          />
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
        <button
          className="btn ml-auto"
          type="submit"
          title="Save"
          aria-label="Save"
          disabled={busy}
        >
          <FontAwesomeIcon icon={faFloppyDisk} />
        </button>
      </div>
    </form>
  );
}
