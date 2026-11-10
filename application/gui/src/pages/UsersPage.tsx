import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faFloppyDisk, faTrash, faXmark } from "@fortawesome/free-solid-svg-icons";
import { useEffect, useState, type FormEvent, type MouseEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ComposerReveal, ErrorNote, NewToggleButton, PageHeader } from "../components/ui";
import type { User } from "../apiTypes";
import { formatDate } from "../format";
import { useApiResource } from "../hooks/useAsyncResource";
import { errorMessage } from "../errors";

export default function UsersPage() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const [showNew, setShowNew] = useState(false);
  const [canCreate, setCanCreate] = useState(false);
  const { data, error, loaded, reload: load, setError } = useApiResource<User[]>("/api/users");
  const users = data ?? [];

  useEffect(() => {
    if (!me?.is_admin) {
      setCanCreate(false);
      return;
    }
    api<{ oauth_provider: string }>("/api/provider")
      .then((p) => setCanCreate(p.oauth_provider === "internal"))
      .catch(() => setCanCreate(false));
  }, [me?.is_admin]);

  async function deleteUser(e: MouseEvent, user: User) {
    e.stopPropagation();
    const label = user.display_name?.trim() || user.email;
    if (!confirm(`Delete ${label}? This cannot be undone.`)) return;
    try {
      await api(`/api/users/${user.id}`, { method: "DELETE" });
      await load();
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div>
      <PageHeader title="Users">
        {canCreate && (
          <NewToggleButton open={showNew} onToggle={() => setShowNew((s) => !s)} />
        )}
      </PageHeader>
      <div className="page-body">
        {!me?.is_admin && (
          <p className="mb-4 text-xs text-faint">
            Directory of authenticated users (for group invites). Providers and join date are
            admin-only.
          </p>
        )}
        <ErrorNote error={error} />
        <ComposerReveal open={showNew && canCreate}>
          <NewUser
            onCreated={() => {
              setShowNew(false);
              void load();
            }}
            onCancel={() => setShowNew(false)}
          />
        </ComposerReveal>
        <div className="panel overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Name</th>
                {me?.is_admin && (
                  <>
                    <th>Providers</th>
                    <th>Joined</th>
                    <th className="table-actions-col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </>
                )}
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr
                  key={u.id}
                  className="cursor-pointer"
                  onClick={() => navigate(`/users/${u.id}`)}
                >
                  <td className="text-ink">
                    <Link
                      to={`/users/${u.id}`}
                      className="row-link"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {u.email}
                    </Link>
                  </td>
                  <td className="text-muted">{u.display_name ?? "—"}</td>
                  {me?.is_admin && (
                    <>
                      <td className="text-muted">
                        {u.providers?.length ? u.providers.join(", ") : "—"}
                      </td>
                      <td className="text-faint">
                        {formatDate(u.created_at)}
                      </td>
                      <td className="table-actions-col">
                        {u.deletable ? (
                          <button
                            className="btn table-row-delete"
                            type="button"
                            title="Delete"
                            aria-label={`Delete ${u.display_name?.trim() || u.email}`}
                            onClick={(e) => void deleteUser(e, u)}
                          >
                            <FontAwesomeIcon icon={faTrash} />
                          </button>
                        ) : (
                          <button
                            className="btn table-row-delete"
                            type="button"
                            title={u.delete_blocked_reason || "Cannot delete"}
                            aria-label={u.delete_blocked_reason || "Cannot delete"}
                            disabled
                            onClick={(e) => e.stopPropagation()}
                          >
                            <FontAwesomeIcon icon={faTrash} />
                          </button>
                        )}
                      </td>
                    </>
                  )}
                </tr>
              ))}
              {!users.length ? (
                <tr>
                  <td colSpan={me?.is_admin ? 5 : 2} className="text-muted">
                    {loaded ? "No users." : "Loading…"}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function NewUser({
  onCreated,
  onCancel,
}: {
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    try {
      await api("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({
          email,
          password,
          display_name: displayName.trim() || null,
        }),
      });
      onCreated();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel composer mb-6" onSubmit={submit}>
      <div className="composer-header">
        <h3 className="composer-title">New user</h3>
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
          <span>Email</span>
          <input
            className="input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoFocus
            autoComplete="off"
          />
        </label>
        <label className="field field-span">
          <span>Display name</span>
          <input
            className="input"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            autoComplete="off"
          />
        </label>
        <label className="field field-span">
          <span>Password</span>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            autoComplete="new-password"
          />
        </label>
      </div>
      <div className="composer-footer">
        <button
          className="btn ml-auto"
          type="submit"
          title="Save"
          aria-label="Save"
          disabled={busy}
        >
          <FontAwesomeIcon icon={faFloppyDisk} />
        </button>
      </div>
    </form>
  );
}
