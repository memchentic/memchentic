import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faArrowLeft,
  faCheck,
  faPen,
  faPlus,
  faRightFromBracket,
  faTrash,
  faUserPlus,
  faXmark,
} from "@fortawesome/free-solid-svg-icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, PageHeader } from "../components/ui";

type SpaceDetail = {
  id: string;
  name: string;
  kind: string;
  created_by: string | null;
  members: { user_id: string; email: string | null; display_name: string | null }[];
};

type DirectoryUser = {
  id: string;
  email: string;
  display_name: string | null;
};

export default function GroupDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { me, refresh } = useAuth();
  const [group, setGroup] = useState<SpaceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const row = await api<SpaceDetail>(`/api/spaces/${id}`);
      setGroup(row);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setGroup(null);
    }
  }, [id]);

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

  const isOwner = !!group && group.created_by === me?.id;
  const canManage = !!me?.is_admin || isOwner;

  function rename() {
    if (!group) return;
    const next = prompt("Rename group", group.name);
    if (next && next !== group.name) {
      void run(() => api(`/api/spaces/${group.id}`, { method: "PATCH", body: JSON.stringify({ name: next }) }));
    }
  }

  const remove = (userId: string) =>
    run(() => api(`/api/spaces/${group!.id}/members/${userId}`, { method: "DELETE" }));

  if (!group && !error) {
    return <p className="text-sm text-muted">Loading…</p>;
  }

  return (
    <div>
      <PageHeader title={group?.name ?? "Group"}>
        <Link to="/groups" className="btn" title="Groups" aria-label="Groups">
          <FontAwesomeIcon icon={faArrowLeft} />
        </Link>
        {group && canManage && (
          <button
            className="btn"
            type="button"
            title="Rename"
            aria-label="Rename"
            onClick={rename}
          >
            <FontAwesomeIcon icon={faPen} />
          </button>
        )}
        {group && canManage && (
          <button
            className="btn"
            type="button"
            title="Delete"
            aria-label="Delete"
            onClick={() => {
              if (!confirm(`Delete ${group.name}? This removes the group and its memories.`)) return;
              void (async () => {
                try {
                  await api(`/api/spaces/${group.id}`, { method: "DELETE" });
                  await refresh();
                  navigate("/groups");
                } catch (err) {
                  setError(err instanceof Error ? err.message : String(err));
                }
              })();
            }}
          >
            <FontAwesomeIcon icon={faTrash} />
          </button>
        )}
        {group && !isOwner && me?.id && group.members.some((m) => m.user_id === me.id) && (
          <button
            className="btn"
            type="button"
            title="Leave"
            aria-label="Leave"
            onClick={() =>
              confirm(`Leave ${group.name}?`) && void remove(me.id).then(() => navigate("/groups"))
            }
          >
            <FontAwesomeIcon icon={faRightFromBracket} />
          </button>
        )}
      </PageHeader>
      <div className="page-body">
      <ErrorNote error={error} />
      {group && (
        <>
          <div className="mb-3 flex items-center justify-between gap-2">
            <h3 className="text-sm font-medium uppercase tracking-wider text-faint">Members</h3>
            {canManage && (
              <button
                className="btn"
                type="button"
                title="Add"
                aria-label="Add"
                onClick={() => setAddOpen(true)}
              >
                <FontAwesomeIcon icon={faUserPlus} />
              </button>
            )}
          </div>
          <div className="panel overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Email</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {group.members.map((m) => (
                  <tr
                    key={m.user_id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/users/${m.user_id}`)}
                  >
                    <td className="text-ink">
                      {m.display_name || m.email || m.user_id}
                      {m.user_id === group.created_by && (
                        <span className="ml-2 text-xs text-faint">creator</span>
                      )}
                    </td>
                    <td className="text-muted">{m.email ?? "—"}</td>
                    <td className="text-right">
                      {canManage && m.user_id !== group.created_by && (
                        <button
                          className="btn"
                          type="button"
                          title="Remove"
                          aria-label="Remove"
                          onClick={(e) => {
                            e.stopPropagation();
                            void remove(m.user_id);
                          }}
                        >
                          <FontAwesomeIcon icon={faTrash} />
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {addOpen && (
            <AddMembersModal
              groupId={group.id}
              memberIds={new Set(group.members.map((m) => m.user_id))}
              onClose={() => setAddOpen(false)}
              onAdded={() => void load()}
            />
          )}
        </>
      )}
      </div>
    </div>
  );
}

function AddMembersModal({
  groupId,
  memberIds,
  onClose,
  onAdded,
}: {
  groupId: string;
  memberIds: Set<string>;
  onClose: () => void;
  onAdded: () => void;
}) {
  const [users, setUsers] = useState<DirectoryUser[]>([]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<DirectoryUser[]>("/api/users")
      .then(setUsers)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return users
      .filter((u) => !memberIds.has(u.id))
      .filter((u) => {
        if (!q) return true;
        return (
          u.email.toLowerCase().includes(q) ||
          (u.display_name ?? "").toLowerCase().includes(q)
        );
      });
  }, [users, memberIds, query]);

  function toggle(userId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  }

  async function done() {
    if (!selected.size) {
      onClose();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      for (const userId of selected) {
        await api(`/api/spaces/${groupId}/members`, {
          method: "POST",
          body: JSON.stringify({ user_id: userId }),
        });
      }
      onAdded();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-members-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div className="flex max-h-[85vh] w-full max-w-lg flex-col border border-border bg-surface shadow-md">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h3 id="add-members-title" className="font-medium text-ink">
            Add members
          </h3>
          <button className="btn" type="button" onClick={onClose} disabled={busy} aria-label="Close">
            <FontAwesomeIcon icon={faXmark} />
          </button>
        </div>
        <div className="border-b border-border px-4 py-3">
          <input
            className="input"
            type="search"
            placeholder="Search users…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
        </div>
        {error && (
          <div className="px-4 pt-3">
            <ErrorNote error={error} />
          </div>
        )}
        <ul className="min-h-0 flex-1 overflow-y-auto divide-y divide-border">
          {!filtered.length && (
            <li className="px-4 py-6 text-center text-sm text-faint">No users to add.</li>
          )}
          {filtered.map((u) => {
            const picked = selected.has(u.id);
            return (
              <li key={u.id} className="flex items-center justify-between gap-3 px-4 py-3">
                <div className="min-w-0">
                  <div className="truncate text-sm text-ink">{u.display_name || u.email}</div>
                  {u.display_name && (
                    <div className="truncate text-xs text-faint">{u.email}</div>
                  )}
                </div>
                <button
                  className="btn shrink-0"
                  type="button"
                  aria-label={picked ? `Deselect ${u.email}` : `Add ${u.email}`}
                  onClick={() => toggle(u.id)}
                  disabled={busy}
                >
                  <FontAwesomeIcon icon={picked ? faCheck : faPlus} />
                </button>
              </li>
            );
          })}
        </ul>
        <div className="flex justify-end gap-2 border-t border-border px-4 py-3">
          <button
            className="btn"
            type="button"
            onClick={onClose}
            disabled={busy}
            title="Cancel"
            aria-label="Cancel"
          >
            <FontAwesomeIcon icon={faXmark} />
          </button>
          <button
            className="btn btn-primary"
            type="button"
            onClick={() => void done()}
            disabled={busy}
            title={
              busy
                ? "Adding…"
                : selected.size
                  ? `Done (${selected.size})`
                  : "Done"
            }
            aria-label={
              busy
                ? "Adding…"
                : selected.size
                  ? `Done (${selected.size})`
                  : "Done"
            }
          >
            <FontAwesomeIcon icon={faCheck} />
          </button>
        </div>
      </div>
    </div>
  );
}
