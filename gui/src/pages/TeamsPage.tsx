import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faPlus } from "@fortawesome/free-solid-svg-icons";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, PageHeader } from "../components/ui";

type Space = { id: string; name: string; kind: string };
type SpaceDetail = Space & {
  created_by: string | null;
  members: { user_id: string; email: string | null; display_name: string | null }[];
};

export default function TeamsPage() {
  const { me, refresh } = useAuth();
  const navigate = useNavigate();
  const [teams, setTeams] = useState<SpaceDetail[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const spaces = await api<Space[]>("/api/spaces");
      const details = await Promise.all(
        spaces.filter((s) => s.kind === "team").map((s) => api<SpaceDetail>(`/api/spaces/${s.id}`)),
      );
      setTeams(details);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(fn: () => Promise<unknown>) {
    try {
      await fn();
      await Promise.all([load(), refresh()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function create(e: FormEvent) {
    e.preventDefault();
    void run(() => api("/api/spaces", { method: "POST", body: JSON.stringify({ name }) })).then(() => setName(""));
  }

  return (
    <div>
      <PageHeader title="Teams" />
      <div className="page-body">
        <ErrorNote error={error} />
        <form className="mb-6 flex gap-2" onSubmit={create}>
          <input
            className="input max-w-sm"
            placeholder="New team name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <button className="btn" type="submit">
            <FontAwesomeIcon icon={faPlus} /> Create
          </button>
        </form>
        {me?.id && teams.length > 0 && (
          <label className="mb-6 flex max-w-sm flex-col gap-1 text-sm text-muted">
            Default shared team
            <select
              className="input"
              value={me.default_shared_space_id ?? ""}
              onChange={(e) =>
                void run(() =>
                  api("/api/me", {
                    method: "PATCH",
                    body: JSON.stringify({ default_shared_space_id: e.target.value || null }),
                  }),
                )
              }
            >
              <option value="">None</option>
              {teams.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
            <span className="text-xs text-faint">
              Agents writing with visibility=shared and no team name land here.
            </span>
          </label>
        )}
        {!teams.length ? (
          <p className="text-sm text-faint">You are not on any teams. Your personal space is always private.</p>
        ) : (
          <div className="panel overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Members</th>
                  <th>Role</th>
                </tr>
              </thead>
              <tbody>
                {teams.map((t) => {
                  const isCreator = t.created_by === me?.id;
                  return (
                    <tr key={t.id} className="cursor-pointer" onClick={() => navigate(`/teams/${t.id}`)}>
                      <td className="font-medium text-ink">{t.name}</td>
                      <td className="text-muted">{t.members.length}</td>
                      <td className="text-muted">
                        {me?.is_admin ? "admin" : isCreator ? "creator" : "member"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
