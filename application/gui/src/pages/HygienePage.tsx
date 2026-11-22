import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faCheck } from "@fortawesome/free-solid-svg-icons";
import { Link } from "react-router-dom";
import { useEffect, useState } from "react";
import { api } from "../api";
import { ErrorNote, PageHeader } from "../components/page";
import { Pager } from "../components/Pager";
import { SpaceScopeSelect } from "../memory/MemoryFilters";
import type { SpaceScope } from "../memory/types";
import { useApiResource } from "../hooks/useAsyncResource";
import { useReloadOnMemoryEvents } from "../hooks/useReloadOnMemoryEvents";
import { errorMessage } from "../errors";
import { formatDateTime } from "../format";

type OverlapPair = {
  a: string;
  b: string;
  title: string;
  reason: string;
  similarity?: number;
};

type ExpiringRow = {
  id: string;
  title: string;
  expires_at: string | null;
};

type OverlapReport = {
  near_duplicates: OverlapPair[];
  conflicts: OverlapPair[];
  expiring: ExpiringRow[];
  scanned: number;
  truncated: boolean;
  note: string;
  near_dup_threshold: number;
  content_trust: string;
};

type SectionId = "conflicts" | "near_duplicates" | "expiring";

const PAGE = 15;

const SECTIONS: { id: SectionId; label: string }[] = [
  { id: "conflicts", label: "Conflicts" },
  { id: "near_duplicates", label: "Near-duplicates" },
  { id: "expiring", label: "Expiring" },
];

function MemoryLink({ id, label }: { id: string; label: string }) {
  return (
    <Link className="memory-title-link" to={`/entries/${id}`}>
      {label}
    </Link>
  );
}

function EmptyNote({ text }: { text: string }) {
  return <p className="text-sm text-faint mb-0">{text}</p>;
}

function slicePage<T>(rows: T[], offset: number): T[] {
  return rows.slice(offset, offset + PAGE);
}

export default function HygienePage() {
  const [spaceScope, setSpaceScope] = useState<SpaceScope>("");
  const [section, setSection] = useState<SectionId>("conflicts");
  const [offset, setOffset] = useState(0);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const qs = new URLSearchParams();
  if (spaceScope) qs.set("space_scope", spaceScope);
  const path = qs.toString() ? `/api/memories/overlap?${qs}` : "/api/memories/overlap";
  const { data, error, loading, reload } = useApiResource<OverlapReport>(path, {
    clearOnError: true,
  });
  useReloadOnMemoryEvents(reload);

  const conflicts = data?.conflicts ?? [];
  const nearDuplicates = data?.near_duplicates ?? [];
  const expiring = data?.expiring ?? [];
  const counts: Record<SectionId, number> = {
    conflicts: conflicts.length,
    near_duplicates: nearDuplicates.length,
    expiring: expiring.length,
  };
  const activeCount = counts[section];
  const conflictPage = slicePage(conflicts, offset);
  const nearDupPage = slicePage(nearDuplicates, offset);
  const expiringPage = slicePage(expiring, offset);
  const pageLen =
    section === "conflicts"
      ? conflictPage.length
      : section === "near_duplicates"
        ? nearDupPage.length
        : expiringPage.length;
  const rangeStart = activeCount === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + pageLen, activeCount);

  useEffect(() => {
    setOffset(0);
  }, [section, spaceScope, data]);

  async function resolveConflict(row: OverlapPair) {
    const key = `conflict:${row.a}:${row.b}`;
    setBusyKey(key);
    setActionError(null);
    try {
      await api("/api/memories/overlap/resolve-conflict", {
        method: "POST",
        body: JSON.stringify({ a: row.a, b: row.b }),
      });
      await reload();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusyKey(null);
    }
  }

  async function resolveNearDup(keep: string, drop: string) {
    const key = `near:${keep}:${drop}`;
    setBusyKey(key);
    setActionError(null);
    try {
      await api("/api/memories/overlap/resolve-near-duplicate", {
        method: "POST",
        body: JSON.stringify({ keep, drop }),
      });
      await reload();
    } catch (err) {
      setActionError(errorMessage(err));
    } finally {
      setBusyKey(null);
    }
  }

  return (
    <div>
      <PageHeader title="Hygiene" />
      <div className="page-body">
        <p className="mb-4 text-sm text-muted">
          Declared conflicts, near-duplicates, and soon-to-expire memories in spaces
          you can read. Pick a category in the toolbar — one list at a time,
          paginated — then resolve or open a memory. Search already hides targets of
          a live supersedes link.
        </p>
        <form className="toolbar mb-6" onSubmit={(e) => e.preventDefault()}>
          <SpaceScopeSelect value={spaceScope} onChange={setSpaceScope} />
          <div className="toolbar-divider" aria-hidden="true" />
          <div
            className="settings-group-picker mb-0"
            role="tablist"
            aria-label="Hygiene category"
          >
            {SECTIONS.map((s) => {
              const active = section === s.id;
              const count = counts[s.id];
              return (
                <button
                  key={s.id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  className={`settings-group-chip ${active ? "settings-group-chip-active" : ""}`}
                  onClick={() => setSection(s.id)}
                >
                  <span>{s.label}</span>
                  <span className="text-faint tabular-nums">{count}</span>
                </button>
              );
            })}
          </div>
        </form>

        <ErrorNote error={error} />
        <ErrorNote error={actionError} />
        {loading && !data ? <p className="text-sm text-muted">Loading…</p> : null}

        {data ? (
          <p className="mb-4 text-xs text-faint">
            Scanned {data.scanned} memories
            {data.truncated ? " (truncated)" : ""}. {data.note}
          </p>
        ) : null}

        {section === "conflicts" ? (
          conflicts.length === 0 ? (
            <EmptyNote text="No declared supersedes pairs." />
          ) : (
            <div className="panel overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Replacement</th>
                    <th>Supersedes</th>
                    <th>Reason</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {conflictPage.map((row) => {
                    const key = `conflict:${row.a}:${row.b}`;
                    return (
                      <tr key={`${row.a}-${row.b}`}>
                        <td>
                          <MemoryLink id={row.a} label={row.title || row.a} />
                        </td>
                        <td>
                          <MemoryLink id={row.b} label={row.b.slice(0, 8)} />
                        </td>
                        <td className="text-sm text-muted">{row.reason}</td>
                        <td>
                          <button
                            type="button"
                            className="btn"
                            disabled={busyKey !== null}
                            title={busyKey === key ? "Resolving…" : "Resolve"}
                            aria-label={busyKey === key ? "Resolving…" : "Resolve"}
                            onClick={() => void resolveConflict(row)}
                          >
                            <FontAwesomeIcon icon={faCheck} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        ) : null}

        {section === "near_duplicates" ? (
          nearDuplicates.length === 0 ? (
            <EmptyNote text="No near-duplicate pairs in the scan window." />
          ) : (
            <div className="panel overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>A</th>
                    <th>B</th>
                    <th>Reason</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {nearDupPage.map((row) => {
                    const keepA = `near:${row.a}:${row.b}`;
                    const keepB = `near:${row.b}:${row.a}`;
                    return (
                      <tr key={`${row.a}-${row.b}`}>
                        <td>{row.title}</td>
                        <td>
                          <MemoryLink id={row.a} label={row.a.slice(0, 8)} />
                        </td>
                        <td>
                          <MemoryLink id={row.b} label={row.b.slice(0, 8)} />
                        </td>
                        <td className="text-sm text-muted">
                          {row.reason}
                          {row.similarity !== undefined ? ` (${row.similarity})` : ""}
                        </td>
                        <td className="whitespace-nowrap">
                          <button
                            type="button"
                            className="btn mr-2"
                            disabled={busyKey !== null}
                            title={busyKey === keepA ? "Keeping…" : "Keep A"}
                            aria-label={busyKey === keepA ? "Keeping…" : "Keep A"}
                            onClick={() => void resolveNearDup(row.a, row.b)}
                          >
                            <FontAwesomeIcon icon={faCheck} />
                            <span className="ml-1.5 text-xs">A</span>
                          </button>
                          <button
                            type="button"
                            className="btn"
                            disabled={busyKey !== null}
                            title={busyKey === keepB ? "Keeping…" : "Keep B"}
                            aria-label={busyKey === keepB ? "Keeping…" : "Keep B"}
                            onClick={() => void resolveNearDup(row.b, row.a)}
                          >
                            <FontAwesomeIcon icon={faCheck} />
                            <span className="ml-1.5 text-xs">B</span>
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        ) : null}

        {section === "expiring" ? (
          expiring.length === 0 ? (
            <EmptyNote text="No upcoming expiries." />
          ) : (
            <div className="panel overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Title</th>
                    <th>Expires</th>
                  </tr>
                </thead>
                <tbody>
                  {expiringPage.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <MemoryLink id={row.id} label={row.title || row.id} />
                      </td>
                      <td className="text-sm text-muted">
                        {row.expires_at ? formatDateTime(row.expires_at) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        ) : null}

        {activeCount > 0 ? (
          <Pager
            label={`${rangeStart}–${rangeEnd} of ${activeCount}`}
            canPrevious={offset > 0}
            canNext={offset + PAGE < activeCount}
            onPrevious={() => setOffset(Math.max(0, offset - PAGE))}
            onNext={() => setOffset(offset + PAGE)}
          />
        ) : null}
      </div>
    </div>
  );
}
