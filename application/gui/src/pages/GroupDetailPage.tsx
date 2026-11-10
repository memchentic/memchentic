import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faArrowLeft,
  faPen,
  faPlus,
  faRightFromBracket,
  faTrash,
  faUserPlus,
} from "@fortawesome/free-solid-svg-icons";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, PageHeader } from "../components/ui";
import type { SpaceDetail } from "../apiTypes";
import { UserPickerModal } from "../components/UserPickerModal";
import { useApiResource } from "../hooks/useAsyncResource";
import { errorMessage } from "../errors";

export default function GroupDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { me, refresh } = useAuth();
  const [addOpen, setAddOpen] = useState(false);
  const {
    data: group,
    error,
    reload: load,
    setError,
  } = useApiResource<SpaceDetail>(id ? `/api/spaces/${id}` : null, { clearOnError: true });

  async function run(fn: () => Promise<unknown>) {
    try {
      await fn();
      await Promise.all([load(), refresh()]);
    } catch (err) {
      setError(errorMessage(err));
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
                  setError(errorMessage(err));
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
            onClick={() => {
              const uid = me.id;
              if (!uid) return;
              if (!confirm(`Leave ${group.name}?`)) return;
              void (async () => {
                try {
                  await api(`/api/spaces/${group.id}/members/${uid}`, {
                    method: "DELETE",
                  });
                  await refresh();
                  navigate("/groups");
                } catch (err) {
                  setError(errorMessage(err));
                }
              })();
            }}
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
                      <Link
                        to={`/users/${m.user_id}`}
                        className="row-link"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {m.display_name || m.email || m.user_id}
                      </Link>
                      {m.user_id === group.created_by && (
                        <span className="ml-2 text-xs text-faint">creator</span>
                      )}
                      {m.pending && (
                        <span className="ml-2 text-xs text-faint">invited</span>
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
                            const label = m.display_name || m.email || m.user_id;
                            if (!confirm(`Remove ${label} from ${group.name}?`)) return;
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
          <UserPickerModal
            open={addOpen}
            titleId="add-members-title"
            excludeIds={new Set(group.members.map((m) => m.user_id))}
            addIcon={faPlus}
            addUser={(userId) =>
              api(`/api/spaces/${group.id}/members`, {
                method: "POST",
                body: JSON.stringify({ user_id: userId }),
              })
            }
            inviteByEmail={(email) =>
              api(`/api/spaces/${group.id}/members`, {
                method: "POST",
                body: JSON.stringify({ email }),
              })
            }
            onClose={() => setAddOpen(false)}
            onAdded={() => void load()}
          />
        </>
      )}
      </div>
    </div>
  );
}
