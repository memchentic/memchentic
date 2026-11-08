import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faFloppyDisk, faTrash, faXmark } from "@fortawesome/free-solid-svg-icons";
import { useCallback, useEffect, useState, type FormEvent, type MouseEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ComposerReveal, ErrorNote, NewToggleButton, PageHeader } from "../components/ui";

type Space = { id: string; name: string; kind: string };
type SpaceDetail = Space & {
  created_by: string | null;
  members: { user_id: string; email: string | null; display_name: string | null }[];
};

export default function GroupsPage() {
  const { me, refresh } = useAuth();
  const navigate = useNavigate();
  const [groups, setGroups] = useState<SpaceDetail[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);

  const load = useCallback(async () => {
    try {
      const spaces = await api<Space[]>("/api/spaces");
      const details = await Promise.all(
        spaces.filter((s) => s.kind === "group").map((s) => api<SpaceDetail>(`/api/spaces/${s.id}`)),
      );
      setGroups(details);
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

  async function deleteGroup(e: MouseEvent, group: SpaceDetail) {
    e.stopPropagation();
    if (!confirm(`Delete ${group.name}? This removes the group and its memories.`)) return;
    try {
      await api(`/api/spaces/${group.id}`, { method: "DELETE" });
      await Promise.all([load(), refresh()]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div>
      <PageHeader title="Groups">
        <NewToggleButton open={showNew} onToggle={() => setShowNew((s) => !s)} />
      </PageHeader>
      <div className="page-body">
        <ErrorNote error={error} />
        <ComposerReveal open={showNew}>
          <NewGroup
            onCreated={() => {
              setShowNew(false);
              void load();
            }}
            onCancel={() => setShowNew(false)}
          />
        </ComposerReveal>
        {me?.id && groups.length > 0 && (
          <label className="mb-6 flex max-w-sm flex-col gap-1 text-sm text-muted">
            Default shared group
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
              {groups.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
            <span className="text-xs text-faint">
              Agents writing with visibility=shared and no group name land here.
            </span>
          </label>
        )}
        {!groups.length ? (
          <p className="text-sm text-faint">You are not on any groups. Your personal space is always private.</p>
        ) : (
          <div className="panel overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Members</th>
                  <th>Role</th>
                  <th className="table-actions-col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {groups.map((t) => {
                  const isCreator = t.created_by === me?.id;
                  const canDelete = !!me?.is_admin || isCreator;
                  return (
                    <tr key={t.id} className="cursor-pointer" onClick={() => navigate(`/groups/${t.id}`)}>
                      <td className="font-medium text-ink">{t.name}</td>
                      <td className="text-muted">{t.members.length}</td>
                      <td className="text-muted">
                        {me?.is_admin ? "admin" : isCreator ? "creator" : "member"}
                      </td>
                      <td className="table-actions-col">
                        {canDelete && (
                          <button
                            className="btn table-row-delete"
                            type="button"
                            title="Delete"
                            aria-label={`Delete ${t.name}`}
                            onClick={(e) => void deleteGroup(e, t)}
                          >
                            <FontAwesomeIcon icon={faTrash} />
                          </button>
                        )}
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

function NewGroup({
  onCreated,
  onCancel,
}: {
  onCreated: () => void;
  onCancel: () => void;
}) {
  const { refresh } = useAuth();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api("/api/spaces", { method: "POST", body: JSON.stringify({ name }) });
      await refresh();
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <form className="panel composer mb-6" onSubmit={submit}>
      <div className="composer-header">
        <h3 className="composer-title">New group</h3>
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
        <label className="field field-span">
          <span>Name</span>
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            autoFocus
          />
        </label>
      </div>
      <div className="composer-footer">
        <button
          className="btn btn-primary ml-auto"
          type="submit"
          title="Save"
          aria-label="Save"
        >
          <FontAwesomeIcon icon={faFloppyDisk} />
        </button>
      </div>
    </form>
  );
}
