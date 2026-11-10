import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faMagnifyingGlass, faXmark } from "@fortawesome/free-solid-svg-icons";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
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
import { applyMemoryEvent, startMemoryLive } from "../liveMemories";
import { errorMessage } from "../errors";
import { NewMemory } from "../memory/NewMemory";
import { Pager } from "../components/Pager";

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
