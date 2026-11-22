import { Link } from "react-router-dom";
import { useState } from "react";
import { ErrorNote, PageHeader } from "../components/page";
import { SpaceScopeSelect } from "../memory/MemoryFilters";
import type { SpaceScope } from "../memory/types";
import { useApiResource } from "../hooks/useAsyncResource";
import { useReloadOnMemoryEvents } from "../hooks/useReloadOnMemoryEvents";
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

export default function HygienePage() {
  const [spaceScope, setSpaceScope] = useState<SpaceScope>("");
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

  return (
    <div>
      <PageHeader title="Hygiene" />
      <div className="page-body">
        <p className="mb-4 text-sm text-muted">
          Read-only report of declared conflicts, near-duplicates, and soon-to-expire
          memories in spaces you can read. Open a memory to revise or forget it;
          search already hides targets of a live supersedes link.
        </p>
        <form className="toolbar mb-6" onSubmit={(e) => e.preventDefault()}>
          <SpaceScopeSelect value={spaceScope} onChange={setSpaceScope} />
        </form>

        <ErrorNote error={error} />
        {loading && !data ? <p className="text-sm text-muted">Loading…</p> : null}

        {data ? (
          <p className="mb-6 text-xs text-faint">
            Scanned {data.scanned} memories
            {data.truncated ? " (truncated)" : ""}. {data.note}
          </p>
        ) : null}

        <section className="mb-8">
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
            Conflicts
          </h3>
          {conflicts.length === 0 ? (
            <EmptyNote text="No declared supersedes pairs." />
          ) : (
            <div className="panel overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Replacement</th>
                    <th>Supersedes</th>
                    <th>Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {conflicts.map((row) => (
                    <tr key={`${row.a}-${row.b}`}>
                      <td>
                        <MemoryLink id={row.a} label={row.title || row.a} />
                      </td>
                      <td>
                        <MemoryLink id={row.b} label={row.b.slice(0, 8)} />
                      </td>
                      <td className="text-sm text-muted">{row.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="mb-8">
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
            Near-duplicates
          </h3>
          {nearDuplicates.length === 0 ? (
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
                  </tr>
                </thead>
                <tbody>
                  {nearDuplicates.map((row) => (
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
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="mb-8">
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted">
            Expiring
          </h3>
          {expiring.length === 0 ? (
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
                  {expiring.map((row) => (
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
          )}
        </section>
      </div>
    </div>
  );
}
