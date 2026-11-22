import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faDownload, faFileImport } from "@fortawesome/free-solid-svg-icons";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api, apiResponse } from "../api";
import { useAuth } from "../auth";
import { ComposerReveal } from "../components/overlay";
import {
  MEMORY_TYPE_TOKENS,
  isUnrestrictedTypes,
  type MemoryRow,
  type MemoryTypeToken,
  type SpaceScope,
} from "../memory/types";
import { ErrorNote, NewToggleButton, PageHeader } from "../components/page";
import { MemoryList } from "../memory/MemoryList";
import { SpaceScopeSelect, TypeFilterMenu } from "../memory/MemoryFilters";
import { formatMemoryQuery, parseMemoryQuery } from "../memory/filterQuery";
import { applyMemoryEvent, startMemoryLive } from "../liveMemories";
import { errorMessage } from "../errors";
import { NewMemory } from "../memory/NewMemory";
import { Pager } from "../components/Pager";

const PAGE = 25;

type FilterState = {
  q: string;
  spaceScope: SpaceScope;
  kind: "" | "entry" | "guide";
  types: MemoryTypeToken[];
  tags: string[];
};

const DEFAULT_FILTERS: FilterState = {
  q: "",
  spaceScope: "",
  kind: "",
  types: [...MEMORY_TYPE_TOKENS],
  tags: [],
};

function filtersFromRaw(raw: string): FilterState {
  const parsed = parseMemoryQuery(raw);
  return {
    q: parsed.q,
    spaceScope: parsed.spaceScope,
    kind: parsed.kind,
    types: parsed.types.length ? parsed.types : [...MEMORY_TYPE_TOKENS],
    tags: parsed.tags,
  };
}

function encodeFilters(f: FilterState): string {
  return formatMemoryQuery({
    q: f.q,
    kind: f.kind,
    types: isUnrestrictedTypes(f.types) ? [] : f.types,
    tags: f.tags,
    spaceScope: f.spaceScope,
  });
}

function typesQuery(selected: MemoryTypeToken[]): string | null {
  if (isUnrestrictedTypes(selected)) return null;
  return selected.join(",");
}

function listKey(f: FilterState): string {
  return [f.q, f.spaceScope, f.kind, f.types.join(","), f.tags.join(",")].join("|");
}

export default function MemoriesPage() {
  const { me } = useAuth();
  const [params, setParams] = useSearchParams();
  const [filters, setFilters] = useState<FilterState>(() =>
    filtersFromRaw(params.get("q") ?? ""),
  );
  const [draft, setDraft] = useState(() => params.get("q") ?? "");
  // The offset belongs to one filter set. When filters change by any route
  // (Apply, chip clear, Back/Forward) paging restarts at 0.
  const [page, setPage] = useState({ key: listKey(filters), offset: 0 });
  const key = listKey(filters);
  const offset = page.key === key ? page.offset : 0;
  const setOffset = (next: number) => setPage({ key, offset: next });
  const [rows, setRows] = useState<MemoryRow[]>([]);
  // True when the last server response filled the page, so a next page may exist.
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // False until the first load attempt has finished.
  const [loaded, setLoaded] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [showIngest, setShowIngest] = useState(false);
  const [ingestText, setIngestText] = useState("");
  const [ingestBusy, setIngestBusy] = useState(false);
  type IngestCandidate = {
    kind: string;
    title: string;
    body: string;
    entry_type?: string;
    confidence?: number;
    reasons?: string[];
    source?: string;
    path_hint?: string;
    repo_hint?: string;
  };
  const [ingestCandidates, setIngestCandidates] = useState<IngestCandidate[]>([]);
  const searching = filters.q.length > 0;

  function ingestTargetSpaceId(): string | undefined {
    if (filters.spaceScope === "group") {
      return me?.default_shared_space_id || undefined;
    }
    return me?.personal_space_id || undefined;
  }

  async function downloadExport() {
    try {
      const qs = new URLSearchParams();
      if (filters.spaceScope) qs.set("space_scope", filters.spaceScope);
      if (filters.kind) qs.set("kind", filters.kind);
      const typeFilter = typesQuery(filters.types);
      if (typeFilter) qs.set("types", typeFilter);
      if (filters.tags.length) qs.set("tags", filters.tags.join(","));
      const res = await apiResponse(`/api/memories/export?${qs.toString()}`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "memories.ndjson";
      a.click();
      URL.revokeObjectURL(url);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function runIngestSuggest() {
    setIngestBusy(true);
    try {
      const row = await api<{ candidates: typeof ingestCandidates }>(
        "/api/memories/ingest/suggest",
        {
          method: "POST",
          body: JSON.stringify({ text: ingestText, use_llm: false }),
        },
      );
      setIngestCandidates(row.candidates);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setIngestBusy(false);
    }
  }

  async function confirmIngestCandidate(c: IngestCandidate) {
    const spaceId = ingestTargetSpaceId();
    try {
      if (c.kind === "guide") {
        await api("/api/guides", {
          method: "POST",
          body: JSON.stringify({
            title: c.title,
            body: c.body,
            space_id: spaceId,
            path_hint: c.path_hint,
            repo_hint: c.repo_hint,
          }),
        });
      } else {
        await api("/api/memories", {
          method: "POST",
          body: JSON.stringify({
            title: c.title,
            body: c.body,
            type: c.entry_type || "note",
            space_id: spaceId,
            path_hint: c.path_hint,
            repo_hint: c.repo_hint,
          }),
        });
      }
      setIngestCandidates((prev) => prev.filter((x) => x !== c));
      void load();
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  function updateIngestCandidate(index: number, patch: Partial<IngestCandidate>) {
    setIngestCandidates((prev) =>
      prev.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    );
  }
  const loadRef = useRef<() => Promise<void>>(async () => {});
  const loadSeq = useRef(0);
  const rowsRef = useRef<MemoryRow[]>([]);
  rowsRef.current = rows;
  const viewRef = useRef({
    searching,
    spaceScope: filters.spaceScope,
    kind: filters.kind,
    types: filters.types,
    tags: filters.tags,
    offset,
    memberships: me?.memberships ?? [],
    meId: me?.id ?? null,
  });

  function writeFilters(next: FilterState, draftText?: string) {
    const encoded = encodeFilters(next);
    setFilters(next);
    setDraft(draftText ?? encoded);
    setPage({ key: listKey(next), offset: 0 });
    if (encoded) setParams({ q: encoded });
    else setParams({});
  }

  const load = useCallback(async () => {
    // Only the newest request may write state; an older, slower response is dropped.
    const seq = ++loadSeq.current;
    const typeFilter = typesQuery(filters.types);
    const qs = new URLSearchParams({
      limit: String(PAGE),
      offset: String(offset),
    });
    if (filters.spaceScope) qs.set("space_scope", filters.spaceScope);
    if (filters.kind) qs.set("kind", filters.kind);
    if (filters.tags.length) qs.set("tags", filters.tags.join(","));
    let path: string;
    if (searching) {
      qs.set("q", filters.q);
      // Search without types returns entries only; always send the selection.
      qs.set("types", typeFilter ?? MEMORY_TYPE_TOKENS.join(","));
      path = `/api/memories/search?${qs}`;
    } else {
      if (typeFilter) qs.set("types", typeFilter);
      path = `/api/memories?${qs}`;
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
  }, [searching, filters, offset]);

  loadRef.current = load;
  viewRef.current = {
    searching,
    spaceScope: filters.spaceScope,
    kind: filters.kind,
    types: filters.types,
    tags: filters.tags,
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
            kind: view.kind,
            types: view.types,
            tags: view.tags,
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
    const raw = params.get("q") ?? "";
    setDraft(raw);
    setFilters(filtersFromRaw(raw));
  }, [params]);

  function applyQueryDraft(raw: string) {
    const parsed = parseMemoryQuery(raw);
    const hasSpaceToken = /\bspace:/.test(raw);
    const next: FilterState = {
      q: parsed.q,
      kind: parsed.kind || (!/\bkind:/.test(raw) ? filters.kind : ""),
      types: parsed.types.length ? parsed.types : filters.types,
      tags: parsed.tags,
      spaceScope: hasSpaceToken ? parsed.spaceScope : filters.spaceScope,
    };
    const encoded = encodeFilters(next);
    setFilters(next);
    setDraft(raw.trim() || encoded);
    setPage({ key: listKey(next), offset: 0 });
    if (encoded) setParams({ q: encoded });
    else setParams({});
  }

  function submitSearch(e: FormEvent) {
    e.preventDefault();
    applyQueryDraft(draft);
  }

  function clearAll() {
    writeFilters(DEFAULT_FILTERS, "");
  }

  const chips: { key: string; label: string; clear: () => void }[] = [];
  if (filters.spaceScope) {
    chips.push({
      key: "space",
      label: `space:${filters.spaceScope}`,
      clear: () => writeFilters({ ...filters, spaceScope: "" }),
    });
  }
  if (filters.kind) {
    chips.push({
      key: "kind",
      label: `kind:${filters.kind}`,
      clear: () => writeFilters({ ...filters, kind: "" }),
    });
  }
  if (!isUnrestrictedTypes(filters.types)) {
    chips.push({
      key: "types",
      label: `types:${filters.types.join(",")}`,
      clear: () => writeFilters({ ...filters, types: [...MEMORY_TYPE_TOKENS] }),
    });
  }
  for (const tag of filters.tags) {
    chips.push({
      key: `tag-${tag}`,
      label: `tag:${tag}`,
      clear: () =>
        writeFilters({ ...filters, tags: filters.tags.filter((t) => t !== tag) }),
    });
  }
  if (filters.q.trim()) {
    chips.push({
      key: "q",
      label: `q:${filters.q.trim()}`,
      clear: () => writeFilters({ ...filters, q: "" }),
    });
  }

  const rangeStart = offset + 1;
  const rangeEnd = offset + rows.length;
  // A locally shortened page (a row was just forgotten) keeps its next page.
  const canNext = hasMore || rows.length >= PAGE;

  // Only the caller's own personal space is a default write target.
  const ownPersonal = me?.personal_space_id ?? "";
  const defaultWriteSpace =
    filters.spaceScope === "group"
      ? (me?.memberships.find((s) => s.kind === "group")?.id ?? ownPersonal)
      : ownPersonal;

  return (
    <div>
      <PageHeader title="Memories">
        <button
          className="btn"
          type="button"
          title="Export NDJSON"
          aria-label="Export NDJSON"
          onClick={() => void downloadExport()}
        >
          <FontAwesomeIcon icon={faDownload} />
        </button>
        <button
          className="btn"
          type="button"
          title="Suggest from text"
          aria-label="Suggest from text"
          aria-pressed={showIngest}
          onClick={() => setShowIngest((s) => !s)}
        >
          <FontAwesomeIcon icon={faFileImport} />
        </button>
        {!searching && (
          <NewToggleButton open={showNew} onToggle={() => setShowNew((s) => !s)} />
        )}
      </PageHeader>
      <div className="page-body">
        {showIngest ? (
          <div className="panel mb-4 grid gap-3 p-4">
            <p className="text-xs text-muted">
              Paste notes or a transcript. Candidates are suggestions only —
              edit if needed, then confirm each one to store it
              {filters.spaceScope === "group" ? " in your shared space" : " in your personal space"}
              .
            </p>
            <textarea
              className="input min-h-32"
              aria-label="Text to suggest memories from"
              value={ingestText}
              onChange={(e) => setIngestText(e.target.value)}
            />
            <div className="flex justify-end gap-2">
              {ingestCandidates.length > 0 ? (
                <button
                  className="btn btn-secondary"
                  type="button"
                  onClick={() => setIngestCandidates([])}
                >
                  Dismiss all
                </button>
              ) : null}
              <button
                className="btn"
                type="button"
                disabled={ingestBusy || !ingestText.trim()}
                onClick={() => void runIngestSuggest()}
              >
                Suggest
              </button>
            </div>
            {ingestCandidates.length > 0 ? (
              <ul className="grid gap-2">
                {ingestCandidates.map((c, i) => (
                  <li key={`${c.title}-${i}`} className="panel p-3 text-sm">
                    <div className="text-xs text-muted mb-1">
                      {c.kind}
                      {c.entry_type ? ` · ${c.entry_type}` : ""}
                      {typeof c.confidence === "number"
                        ? ` · ${Math.round(c.confidence * 100)}%`
                        : ""}
                      {c.source ? ` · ${c.source}` : ""}
                    </div>
                    {c.reasons && c.reasons.length > 0 ? (
                      <div className="text-xs text-muted mb-2">{c.reasons.join(" · ")}</div>
                    ) : null}
                    <input
                      className="input mb-2"
                      aria-label={`Candidate ${i + 1} title`}
                      value={c.title}
                      onChange={(e) => updateIngestCandidate(i, { title: e.target.value })}
                    />
                    {c.kind === "entry" ? (
                      <select
                        className="input mb-2"
                        aria-label={`Candidate ${i + 1} type`}
                        value={c.entry_type || "note"}
                        onChange={(e) =>
                          updateIngestCandidate(i, { entry_type: e.target.value })
                        }
                      >
                        {MEMORY_TYPE_TOKENS.map((t) => (
                          <option key={t} value={t}>
                            {t}
                          </option>
                        ))}
                      </select>
                    ) : null}
                    <textarea
                      className="input min-h-20 font-sans text-xs"
                      aria-label={`Candidate ${i + 1} body`}
                      value={c.body}
                      onChange={(e) => updateIngestCandidate(i, { body: e.target.value })}
                    />
                    <div className="mt-2 flex gap-2">
                      <button
                        className="btn"
                        type="button"
                        onClick={() => void confirmIngestCandidate(c)}
                      >
                        Remember
                      </button>
                      <button
                        className="btn btn-secondary"
                        type="button"
                        onClick={() =>
                          setIngestCandidates((prev) => prev.filter((_, j) => j !== i))
                        }
                      >
                        Dismiss
                      </button>
                    </div>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
        <form className="toolbar metrics-filter-bar mb-3" onSubmit={submitSearch}>
          <div className="toolbar-search metrics-query-wrap">
            <input
              className="input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Filter: kind:entry type:fact tag:ops space:personal …"
              aria-label="Search memories"
            />
            <button type="submit" className="btn btn-secondary">
              Apply
            </button>
          </div>
        </form>

        <div className="toolbar metrics-filter-bar mb-3">
          <SpaceScopeSelect
            value={filters.spaceScope}
            onChange={(spaceScope) => {
              setFilters((p) => ({ ...p, spaceScope }));
              setOffset(0);
            }}
          />
          <label className="field toolbar-field">
            <span>Kind</span>
            <select
              className="input w-auto"
              value={filters.kind}
              onChange={(e) => {
                setFilters((p) => ({
                  ...p,
                  kind: e.target.value as "" | "entry" | "guide",
                }));
                setOffset(0);
              }}
            >
              <option value="">All</option>
              <option value="entry">Entry</option>
              <option value="guide">Guide</option>
            </select>
          </label>
          <TypeFilterMenu
            selected={filters.types}
            onChange={(types) => {
              setFilters((p) => ({ ...p, types }));
              setOffset(0);
            }}
          />
          <button type="button" className="btn btn-ghost text-sm" onClick={clearAll}>
            Clear
          </button>
        </div>

        {chips.length > 0 ? (
          <ul className="metrics-filter-chips mb-4">
            {chips.map((chip) => (
              <li key={chip.key}>
                <button type="button" className="metrics-filter-chip" onClick={chip.clear}>
                  {chip.label}
                  <span aria-hidden="true"> ×</span>
                </button>
              </li>
            ))}
          </ul>
        ) : null}

        {searching && (
          <p className="mb-4 text-xs text-faint">
            Showing hybrid matches for “{filters.q}” — full-text and vector similarity fused.
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

        <Pager
          label={
            rows.length === 0
              ? !loaded
                ? "Loading…"
                : searching
                  ? "No matches"
                  : "No rows"
              : `${rangeStart}–${rangeEnd}${searching && canNext ? "+" : ""}`
          }
          canPrevious={offset !== 0}
          canNext={canNext}
          onPrevious={() => setOffset(Math.max(0, offset - PAGE))}
          onNext={() => setOffset(offset + PAGE)}
        />
      </div>
    </div>
  );
}
