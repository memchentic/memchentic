import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faMagnifyingGlass } from "@fortawesome/free-solid-svg-icons";
import { useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { ErrorNote, MemoryList, PageHeader, SpaceSelect, type MemoryRow } from "../components/ui";

export default function SearchPage() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const [space, setSpace] = useState("");
  const [kind, setKind] = useState("entry");
  const [rows, setRows] = useState<MemoryRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!q.trim()) return;
    setParams({ q });
    const qs = new URLSearchParams({ q, kind });
    if (space) qs.set("space_id", space);
    try {
      setRows(await api<MemoryRow[]>(`/api/memories/search?${qs}`));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div>
      <PageHeader title="Search" />
      <div className="page-body">
        <form className="mb-6 flex flex-wrap gap-2" onSubmit={submit}>
          <input className="input min-w-64 flex-1" placeholder="Keyword or natural language" value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
          <SpaceSelect value={space} onChange={setSpace} />
          <select className="input w-auto" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="entry">Entries</option>
            <option value="guide">Guides</option>
          </select>
          <button className="btn" type="submit">
            <FontAwesomeIcon icon={faMagnifyingGlass} /> Find
          </button>
        </form>
        <p className="mb-4 text-xs text-faint">Hybrid ranking: full-text and vector similarity fused.</p>
        <ErrorNote error={error} />
        {rows && <MemoryList rows={rows} empty="No matches." />}
      </div>
    </div>
  );
}
