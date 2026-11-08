import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faArrowLeft,
  faPlus,
  faTrash,
  faUserPlus,
  faXmark,
} from "@fortawesome/free-solid-svg-icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, PageHeader } from "../components/ui";

type UserDetail = {
  id: string;
  email: string;
  display_name: string | null;
  providers?: string[];
  created_at?: string | null;
  deletable?: boolean;
  delete_blocked_reason?: string | null;
  groups: { id: string; name: string; kind: string }[];
};

type SpaceDetail = {
  id: string;
  name: string;
  kind: string;
  created_by: string | null;
  members: { user_id: string }[];
};

/** OAuth / IdP names that usually carry a human profile name. */
const PROFILE_PROVIDERS = new Set([
  "google",
  "github",
  "okta",
  "authentik",
  "keycloak",
  "auth0",
  "entra",
  "gitlab",
  "generic",
]);

function emailLocalPart(email: string): string {
  const local = email.split("@")[0]?.trim();
  return local || email;
}

/** Prefer IdP display name; fall back to email local-part (same as API defaults). */
function resolveDisplayName(user: Pick<UserDetail, "email" | "display_name">): string {
  const named = user.display_name?.trim();
  return named || emailLocalPart(user.email);
}

/**
 * Whether the profile leans on an external IdP (name-first) vs email/password.
 *
 * Admins see `providers`; when absent, treat as email-primary (invite directory).
 */
function prefersProfileName(providers: string[] | undefined): boolean {
  if (!providers?.length) return false;
  return providers.some((p) => PROFILE_PROVIDERS.has(p));
}

export default function UserDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { me } = useAuth();
  const [user, setUser] = useState<UserDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [manageableCount, setManageableCount] = useState(0);

  const load = useCallback(async () => {
    if (!id) return;
    try {
      const row = await api<UserDetail>(`/api/users/${id}`);
      setUser(row);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setUser(null);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!me?.id && !me?.is_admin) {
      setManageableCount(0);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const spaces = await api<{ id: string; kind: string }[]>("/api/spaces");
        const groups = await Promise.all(
          spaces
            .filter((s) => s.kind === "group")
            .map((s) => api<SpaceDetail>(`/api/spaces/${s.id}`)),
        );
        if (cancelled) return;
        const n = groups.filter(
          (t) => me?.is_admin || t.created_by === me?.id,
        ).length;
        setManageableCount(n);
      } catch {
        if (!cancelled) setManageableCount(0);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [me?.id, me?.is_admin]);

  if (!user && !error) {
    return <p className="text-sm text-muted">Loading…</p>;
  }

  const name = user ? resolveDisplayName(user) : "User";
  const email = user?.email ?? "";
  const nameFirst = prefersProfileName(user?.providers);
  const canAddToGroup = manageableCount > 0;

  async function deleteThisUser() {
    if (!user) return;
    if (!confirm(`Delete ${name}? This cannot be undone.`)) return;
    try {
      await api(`/api/users/${user.id}`, { method: "DELETE" });
      navigate("/users");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div>
      <PageHeader title={name}>
        <Link to="/users" className="btn" title="Users" aria-label="Users">
          <FontAwesomeIcon icon={faArrowLeft} />
        </Link>
        {me?.is_admin && user && (
          <button
            className="btn"
            type="button"
            title={user.deletable ? "Delete" : user.delete_blocked_reason || "Cannot delete"}
            aria-label={user.deletable ? "Delete" : user.delete_blocked_reason || "Cannot delete"}
            disabled={!user.deletable}
            onClick={() => void deleteThisUser()}
          >
            <FontAwesomeIcon icon={faTrash} />
          </button>
        )}
      </PageHeader>
      <div className="page-body">
        <ErrorNote error={error} />
        {user && (
          <>
            <div className="panel mb-6 overflow-x-auto">
              <table className="data-table">
                <tbody>
                  {nameFirst ? (
                    <>
                      <tr>
                        <th className="w-36 text-faint">Name</th>
                        <td className="text-ink">{name}</td>
                      </tr>
                      <tr>
                        <th className="text-faint">Email</th>
                        <td className="text-muted">{email}</td>
                      </tr>
                    </>
                  ) : (
                    <>
                      <tr>
                        <th className="w-36 text-faint">Email</th>
                        <td className="text-ink">{email}</td>
                      </tr>
                      <tr>
                        <th className="text-faint">Name</th>
                        <td className="text-muted">{name}</td>
                      </tr>
                    </>
                  )}
                  {me?.is_admin && (
                    <>
                      <tr>
                        <th className="text-faint">Providers</th>
                        <td className="text-muted">
                          {user.providers?.length ? user.providers.join(", ") : "—"}
                        </td>
                      </tr>
                      <tr>
                        <th className="text-faint">Joined</th>
                        <td className="text-faint">
                          {user.created_at
                            ? new Date(user.created_at).toLocaleDateString()
                            : "—"}
                        </td>
                      </tr>
                    </>
                  )}
                </tbody>
              </table>
            </div>
            <div className="mb-3 flex items-center justify-between gap-2">
              <h3 className="text-sm font-medium uppercase tracking-wider text-faint">
                Groups
              </h3>
              {canAddToGroup && (
                <button
                  className="btn"
                  type="button"
                  title="Add to group"
                  aria-label="Add to group"
                  onClick={() => setAddOpen(true)}
                >
                  <FontAwesomeIcon icon={faUserPlus} />
                </button>
              )}
            </div>
            {!user.groups.length ? (
              <p className="text-sm text-faint">No shared groups visible.</p>
            ) : (
              <div className="panel overflow-x-auto">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Name</th>
                    </tr>
                  </thead>
                  <tbody>
                    {user.groups.map((t) => (
                      <tr
                        key={t.id}
                        className="cursor-pointer"
                        onClick={() => navigate(`/groups/${t.id}`)}
                      >
                        <td className="text-ink">{t.name}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {addOpen && (
              <AddToGroupModal
                userId={user.id}
                userLabel={name}
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

function AddToGroupModal({
  userId,
  userLabel,
  onClose,
  onAdded,
}: {
  userId: string;
  userLabel: string;
  onClose: () => void;
  onAdded: () => void;
}) {
  const { me } = useAuth();
  const [groups, setGroups] = useState<SpaceDetail[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadGroups = useCallback(async () => {
    setLoading(true);
    try {
      const spaces = await api<{ id: string; kind: string }[]>("/api/spaces");
      const details = await Promise.all(
        spaces
          .filter((s) => s.kind === "group")
          .map((s) => api<SpaceDetail>(`/api/spaces/${s.id}`)),
      );
      setGroups(details);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setGroups([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadGroups();
  }, [loadGroups]);

  const candidates = useMemo(() => {
    return groups
      .filter((t) => me?.is_admin || t.created_by === me?.id)
      .filter((t) => !t.members.some((m) => m.user_id === userId))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [groups, me?.id, me?.is_admin, userId]);

  async function addTo(groupId: string) {
    setBusyId(groupId);
    setError(null);
    try {
      await api(`/api/spaces/${groupId}/members`, {
        method: "POST",
        body: JSON.stringify({ user_id: userId }),
      });
      onAdded();
      await loadGroups();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-to-group-title"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busyId) onClose();
      }}
    >
      <div className="flex max-h-[85vh] w-full max-w-lg flex-col border border-border bg-surface shadow-md">
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h3 id="add-to-group-title" className="font-medium text-ink">
            Add {userLabel} to a group
          </h3>
          <button
            className="btn"
            type="button"
            onClick={onClose}
            disabled={!!busyId}
            aria-label="Close"
          >
            <FontAwesomeIcon icon={faXmark} />
          </button>
        </div>
        {error && (
          <div className="px-4 pt-3">
            <ErrorNote error={error} />
          </div>
        )}
        <ul className="min-h-0 flex-1 overflow-y-auto divide-y divide-border">
          {loading && (
            <li className="px-4 py-6 text-center text-sm text-faint">Loading groups…</li>
          )}
          {!loading && !candidates.length && (
            <li className="px-4 py-6 text-center text-sm text-faint">
              No groups you manage that they are not already on.
            </li>
          )}
          {candidates.map((t) => (
            <li key={t.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="min-w-0 truncate text-sm text-ink">{t.name}</div>
              <button
                className="btn shrink-0"
                type="button"
                aria-label={`Add to ${t.name}`}
                onClick={() => void addTo(t.id)}
                disabled={!!busyId}
              >
                <FontAwesomeIcon icon={faPlus} />
              </button>
            </li>
          ))}
        </ul>
        <div className="flex justify-end border-t border-border px-4 py-3">
          <button
            className="btn"
            type="button"
            title="Close"
            aria-label="Close"
            onClick={onClose}
            disabled={!!busyId}
          >
            <FontAwesomeIcon icon={faXmark} />
          </button>
        </div>
      </div>
    </div>
  );
}
