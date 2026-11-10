import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faArrowLeft,
  faBan,
  faPlus,
  faTrash,
  faUserPlus,
  faXmark,
} from "@fortawesome/free-solid-svg-icons";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, Modal, PageHeader } from "../components/ui";
import type { UserDetail } from "../apiTypes";
import { formatDate } from "../format";
import { useApiResource } from "../hooks/useAsyncResource";
import { useGroupDetails } from "../hooks/useGroupDetails";
import { errorMessage } from "../errors";

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
  const [addOpen, setAddOpen] = useState(false);
  // Text shown after a successful revoke; null before.
  const [revokedNote, setRevokedNote] = useState<string | null>(null);

  const {
    data: user,
    error,
    reload: load,
    setError,
  } = useApiResource<UserDetail>(id ? `/api/users/${id}` : null, { clearOnError: true });
  const canLoadGroups = Boolean(me?.id || me?.is_admin);
  const manageable = useGroupDetails(canLoadGroups, { clearOnError: true });
  const manageableCount = canLoadGroups
    ? (manageable.data ?? []).filter((t) => me?.is_admin || t.created_by === me?.id).length
    : 0;

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
      setError(errorMessage(err));
    }
  }

  async function revokeCredentials() {
    if (!user) return;
    if (!confirm(`End all sessions and revoke all API keys of ${name}? The account is kept.`)) {
      return;
    }
    try {
      const res = await api<{ access_tokens_expire_within_seconds: number }>(
        `/api/users/${user.id}/revoke-credentials`,
        { method: "POST" },
      );
      const minutes = Math.ceil(res.access_tokens_expire_within_seconds / 60);
      setRevokedNote(
        `Sessions and API keys revoked. An access token that is already issued stays valid for up to ${minutes} minutes.`,
      );
      setError(null);
    } catch (err) {
      setRevokedNote(null);
      setError(errorMessage(err));
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
            title="Revoke sessions and keys"
            aria-label="Revoke sessions and keys"
            onClick={() => void revokeCredentials()}
          >
            <FontAwesomeIcon icon={faBan} />
          </button>
        )}
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
        {revokedNote && <p className="mb-4 text-sm text-muted">{revokedNote}</p>}
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
                          {formatDate(user.created_at)}
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
                        <td className="text-ink">
                          <Link
                            to={`/groups/${t.id}`}
                            className="row-link"
                            onClick={(e) => e.stopPropagation()}
                          >
                            {t.name}
                          </Link>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <AddToGroupModal
              open={addOpen}
              userId={user.id}
              userLabel={name}
              onClose={() => setAddOpen(false)}
              onAdded={() => void load()}
            />
          </>
        )}
      </div>
    </div>
  );
}

function AddToGroupModal({
  open,
  userId,
  userLabel,
  onClose,
  onAdded,
}: {
  open: boolean;
  userId: string;
  userLabel: string;
  onClose: () => void;
  onAdded: () => void;
}) {
  const { me } = useAuth();
  const [busyId, setBusyId] = useState<string | null>(null);
  const {
    data: groups,
    error,
    loading,
    reload: loadGroups,
    setError,
  } = useGroupDetails(open, { clearOnError: true });

  const candidates = useMemo(() => {
    return (groups ?? [])
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
      setError(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      labelledBy="add-to-group-title"
      closeDisabled={!!busyId}
    >
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
    </Modal>
  );
}
