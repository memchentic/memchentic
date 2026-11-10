import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faCheck,
  faShieldHalved,
  faToggleOff,
  faToggleOn,
  faTrash,
  faUserGroup,
  faUserPlus,
  faUsers,
  faXmark,
} from "@fortawesome/free-solid-svg-icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, Modal, PageHeader } from "../components/ui";

type AuthGroup = {
  id: string;
  slug: string;
  name: string;
  system: boolean;
  member_count: number;
  roster_bound?: boolean;
  idp_group_id?: string | null;
  idp_group_name?: string | null;
};

type AuthMember = {
  user_id: string;
  email: string | null;
  display_name: string | null;
  locked: boolean;
  source?: string;
};

type DirectoryUser = {
  id: string;
  email: string;
  display_name: string | null;
};

type IdpGroupOption = { id: string; name: string };

type IdpGroupsResponse = {
  provider: string;
  supports_groups: boolean;
  groups: IdpGroupOption[];
  error?: string | null;
};

const CATEGORIES = [
  {
    id: "auth-groups",
    label: "Auth Groups",
    icon: faUserGroup,
  },
  {
    id: "registration",
    label: "Registration",
    icon: faToggleOn,
  },
] as const;

export default function SettingsPage() {
  const { me } = useAuth();
  const [params, setParams] = useSearchParams();
  const category = params.get("category") || "auth-groups";

  if (!me?.is_admin) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="settings-page">
      <PageHeader title="System Settings" />
      <div className="settings-columns">
        <aside className="settings-rail" aria-label="Settings categories">
          <ul className="settings-rail-list">
            {CATEGORIES.map((c) => {
              const active = category === c.id;
              return (
                <li key={c.id} className="settings-rail-item">
                  <button
                    type="button"
                    className={`settings-rail-row ${active ? "settings-rail-row-active" : ""}`}
                    onClick={() => setParams({ category: c.id })}
                  >
                    <FontAwesomeIcon icon={c.icon} className="settings-rail-icon" />
                    <span className="truncate">{c.label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>
        <section className="settings-main min-w-0 flex-1">
          {category === "auth-groups" ? (
            <div key="auth-groups" className="settings-main-pane">
              <AuthGroupsPanel />
            </div>
          ) : null}
          {category === "registration" ? (
            <div key="registration" className="settings-main-pane">
              <RegistrationPanel />
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}

function RegistrationPanel() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const row = await api<{ enabled: boolean }>("/api/system/registration");
      setEnabled(row.enabled);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function setRegistration(next: boolean) {
    setBusy(true);
    try {
      const row = await api<{ enabled: boolean }>("/api/system/registration", {
        method: "PUT",
        body: JSON.stringify({ enabled: next }),
      });
      setEnabled(row.enabled);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-body settings-panel-body">
      <ErrorNote error={error} />
      <div className="mb-3">
        <h3 className="text-sm font-medium text-ink">Public registration</h3>
        <p className="text-xs text-muted mt-0.5">
          When disabled, the sign-in page hides Register, and a first-time
          single sign-on user is refused unless an auth group IdP bind admits
          them. When the Users auth group has an IdP bind, only members of a
          bound IdP group are admitted, whatever this setting is.
          Administrators can still create users from Users.{" "}
          <code className="text-ink">REGISTER_ENABLED</code> only sets the value on
          the first boot; after that, this setting is kept across restarts.
        </p>
      </div>
      <div className="settings-group-picker" role="group" aria-label="Registration">
        <button
          type="button"
          className={`settings-group-chip ${enabled === true ? "settings-group-chip-active" : ""}`}
          disabled={busy || enabled === null}
          onClick={() => void setRegistration(true)}
        >
          <FontAwesomeIcon icon={faToggleOn} className="opacity-70" />
          <span>Enabled</span>
        </button>
        <button
          type="button"
          className={`settings-group-chip ${enabled === false ? "settings-group-chip-active" : ""}`}
          disabled={busy || enabled === null}
          onClick={() => void setRegistration(false)}
        >
          <FontAwesomeIcon icon={faToggleOff} className="opacity-70" />
          <span>Disabled</span>
        </button>
      </div>
    </div>
  );
}

function AuthGroupsPanel() {
  const navigate = useNavigate();
  const { refresh } = useAuth();
  const [groups, setGroups] = useState<AuthGroup[]>([]);
  const [slug, setSlug] = useState<string>("administrators");
  const [members, setMembers] = useState<AuthMember[]>([]);
  // Slug that the `members` rows belong to; null until the first roster has loaded.
  const [membersSlug, setMembersSlug] = useState<string | null>(null);
  const slugRef = useRef(slug);
  slugRef.current = slug;
  const [idpCatalog, setIdpCatalog] = useState<IdpGroupsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [bindBusy, setBindBusy] = useState(false);
  // Value picked in the "Bound IdP group" select but not applied yet; null
  // means "show the value stored on the server".
  const [bindDraft, setBindDraft] = useState<string | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  const loadGroups = useCallback(async () => {
    const rows = await api<AuthGroup[]>("/api/auth-groups");
    setGroups(rows);
    if (!rows.some((g) => g.slug === slug) && rows[0]) {
      setSlug(rows[0].slug);
    }
  }, [slug]);

  const loadMembers = useCallback(async () => {
    if (!slug) return;
    const rows = await api<AuthMember[]>(`/api/auth-groups/${slug}/members`);
    // Drop the answer when another group was selected while it was in flight.
    if (slugRef.current !== slug) return;
    setMembers(rows);
    setMembersSlug(slug);
  }, [slug]);

  const reload = useCallback(async () => {
    try {
      await loadGroups();
      await loadMembers();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [loadGroups, loadMembers]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    // The IdP directory is an external call: fetch it once per panel mount,
    // not on every roster reload.
    let cancelled = false;
    api<IdpGroupsResponse>("/api/auth-groups/idp-groups")
      .then((catalog) => {
        if (!cancelled) setIdpCatalog(catalog);
      })
      .catch((err) => {
        if (!cancelled) setCatalogError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const active = groups.find((g) => g.slug === slug) ?? null;
  const rosterBound = Boolean(active?.roster_bound);
  // Show a roster only under the heading of the group it was loaded for.
  const membersReady = membersSlug === slug;
  const visibleMembers = membersReady ? members : [];

  async function remove(userId: string, label: string) {
    if (!slug || rosterBound) return;
    const groupLabel = active?.name ?? slug;
    if (!confirm(`Remove ${label} from ${groupLabel}?`)) return;
    try {
      await api(`/api/auth-groups/${slug}/members/${userId}`, { method: "DELETE" });
      await Promise.all([reload(), refresh()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function setBind(value: string) {
    if (!slug) return;
    setBindBusy(true);
    try {
      if (!value) {
        await api(`/api/auth-groups/${slug}/idp-bind`, {
          method: "PUT",
          body: JSON.stringify({ idp_group_id: null, idp_group_name: null }),
        });
      } else {
        const opt = idpCatalog?.groups.find((g) => g.id === value);
        await api(`/api/auth-groups/${slug}/idp-bind`, {
          method: "PUT",
          body: JSON.stringify({
            idp_group_id: value,
            idp_group_name: opt?.name || value,
          }),
        });
      }
      await reload();
      setBindDraft(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBindBusy(false);
    }
  }

  const boundId = active?.idp_group_id || "";
  const bindValue = bindDraft ?? boundId;
  const bindDirty = bindValue !== boundId;

  function applyBind() {
    if (!active || !bindDirty) return;
    const target = idpCatalog?.groups.find((g) => g.id === bindValue)?.name || bindValue;
    const question = bindValue
      ? `Bind ${active.name} to the IdP group "${target}"? Membership is then synced from the IdP at sign-in, and manual add and remove are disabled.`
      : `Clear the IdP bind of ${active.name}? Membership becomes manual again.`;
    if (!confirm(question)) return;
    void setBind(bindValue);
  }

  return (
    <div className="page-body settings-panel-body">
      <ErrorNote error={error} />
      {idpCatalog?.error || catalogError ? (
        <p className="text-xs text-muted mb-3" role="status">
          IdP groups: {idpCatalog?.error || catalogError}
        </p>
      ) : null}
      <div className="settings-group-picker">
        {groups.map((g) => {
          const selected = g.slug === slug;
          const icon = g.slug === "administrators" ? faShieldHalved : faUsers;
          return (
            <button
              key={g.slug}
              type="button"
              className={`settings-group-chip ${selected ? "settings-group-chip-active" : ""}`}
              onClick={() => {
                setSlug(g.slug);
                setBindDraft(null);
              }}
            >
              <FontAwesomeIcon icon={icon} className="opacity-70" />
              <span>{g.name}</span>
              <span className="text-faint text-xs">{g.member_count}</span>
            </button>
          );
        })}
      </div>

      {active && (
        <>
          <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
            <div className="min-w-0 flex-1">
              <h3 className="text-sm font-medium text-ink">{active.name}</h3>
              <p className="text-xs text-muted mt-0.5">
                {rosterBound
                  ? "Synced from IdP — clear bind to edit manually."
                  : "Fixed system roster — add or remove members only."}
              </p>
              <label
                className="mt-3 block text-xs font-medium text-ink"
                htmlFor="idp-bind-select"
              >
                Bound IdP group
              </label>
              <div className="mt-1 flex max-w-md items-center gap-2">
                <select
                  id="idp-bind-select"
                  className="input flex-1"
                  disabled={bindBusy}
                  value={bindValue}
                  onChange={(e) => setBindDraft(e.target.value)}
                >
                  <option value="">None</option>
                  {(idpCatalog?.groups || []).map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))}
                  {active.idp_group_id &&
                  !(idpCatalog?.groups || []).some((g) => g.id === active.idp_group_id) ? (
                    <option value={active.idp_group_id}>
                      {active.idp_group_name || active.idp_group_id}
                    </option>
                  ) : null}
                </select>
                <button
                  className="btn"
                  type="button"
                  title="Apply bind"
                  aria-label="Apply bind"
                  disabled={bindBusy || !bindDirty}
                  onClick={applyBind}
                >
                  <FontAwesomeIcon icon={faCheck} />
                </button>
              </div>
              {bindDirty ? (
                <p className="text-xs text-faint mt-1" role="status">
                  Not applied yet. Select Apply bind to save.
                </p>
              ) : null}
              {idpCatalog && !idpCatalog.supports_groups ? (
                <p className="text-xs text-faint mt-1">
                  Current OAuth provider has no directory groups to bind.
                </p>
              ) : null}
            </div>
            {!rosterBound ? (
              <button
                className="btn"
                type="button"
                aria-label="Add"
                title="Add"
                onClick={() => setAddOpen(true)}
              >
                <FontAwesomeIcon icon={faUserPlus} />
              </button>
            ) : null}
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
                {visibleMembers.map((m) => (
                  <tr
                    key={m.user_id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/users/${m.user_id}`)}
                  >
                    <td className="text-ink">
                      {m.display_name || m.email || m.user_id}
                      {m.locked ? (
                        <span className="ml-2 text-xs text-faint">locked</span>
                      ) : null}
                    </td>
                    <td className="text-muted">{m.email ?? "—"}</td>
                    <td className="text-right">
                      {!m.locked && !rosterBound ? (
                        <button
                          className="btn"
                          type="button"
                          aria-label="Remove"
                          title="Remove"
                          onClick={(e) => {
                            e.stopPropagation();
                            void remove(
                              m.user_id,
                              m.display_name || m.email || m.user_id,
                            );
                          }}
                        >
                          <FontAwesomeIcon icon={faTrash} />
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
                {!visibleMembers.length ? (
                  <tr>
                    <td colSpan={3} className="text-muted">
                      {membersReady ? "No members yet." : "Loading…"}
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
          <AddAuthMembersModal
            open={addOpen && !rosterBound}
            slug={slug}
            memberIds={new Set(visibleMembers.map((m) => m.user_id))}
            onClose={() => setAddOpen(false)}
            onAdded={() => void Promise.all([reload(), refresh()])}
          />
        </>
      )}
    </div>
  );
}

function AddAuthMembersModal({
  open,
  slug,
  memberIds,
  onClose,
  onAdded,
}: {
  open: boolean;
  slug: string;
  memberIds: Set<string>;
  onClose: () => void;
  onAdded: () => void;
}) {
  const [users, setUsers] = useState<DirectoryUser[]>([]);
  const [lookupHit, setLookupHit] = useState<DirectoryUser | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLookupHit(null);
    setQuery("");
    setSelected(new Set());
    setError(null);
    setBusy(false);
    api<DirectoryUser[]>("/api/users")
      .then(setUsers)
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const q = query.trim();
    const looksLikeEmail = q.includes("@") && !q.includes(" ");
    if (!looksLikeEmail) {
      setLookupHit(null);
      return;
    }
    const already = users.some((u) => u.email.toLowerCase() === q.toLowerCase());
    if (already) {
      setLookupHit(null);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void api<DirectoryUser>(`/api/users/lookup?email=${encodeURIComponent(q)}`)
        .then((hit) => {
          if (!cancelled) setLookupHit(hit);
        })
        .catch(() => {
          if (!cancelled) setLookupHit(null);
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [open, query, users]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = users
      .filter((u) => !memberIds.has(u.id))
      .filter((u) => {
        if (!q) return true;
        return (
          u.email.toLowerCase().includes(q) ||
          (u.display_name ?? "").toLowerCase().includes(q)
        );
      });
    if (
      lookupHit &&
      !memberIds.has(lookupHit.id) &&
      !rows.some((u) => u.id === lookupHit.id)
    ) {
      return [...rows, lookupHit];
    }
    return rows;
  }, [users, memberIds, query, lookupHit]);

  function toggle(userId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  }

  async function done() {
    if (busy) return;
    if (!selected.size) {
      onClose();
      return;
    }
    setBusy(true);
    setError(null);
    let added = 0;
    try {
      for (const userId of selected) {
        await api(`/api/auth-groups/${slug}/members`, {
          method: "POST",
          body: JSON.stringify({ user_id: userId }),
        });
        added += 1;
        // Drop the user from the selection so a retry after a later failure
        // does not post them again.
        setSelected((prev) => {
          const next = new Set(prev);
          next.delete(userId);
          return next;
        });
      }
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      // Refresh the roster even after a partial failure.
      if (added > 0) onAdded();
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      labelledBy="add-auth-members-title"
      closeDisabled={busy}
    >
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h3 id="add-auth-members-title" className="font-medium text-ink">
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
      <p className="px-4 pt-3 text-xs text-muted">
        {slug === "administrators"
          ? "A user is in one auth group only. Adding a user here removes them from Users."
          : "A user is in one auth group only. Adding a user here removes them from Administrators."}
      </p>
      {error ? (
        <div className="px-4 pt-3">
          <ErrorNote error={error} />
        </div>
      ) : null}
      <ul className="min-h-0 flex-1 overflow-y-auto divide-y divide-border">
        {!filtered.length ? (
          <li className="px-4 py-6 text-center text-sm text-faint">No users to add.</li>
        ) : null}
        {filtered.map((u) => {
          const picked = selected.has(u.id);
          return (
            <li key={u.id} className="flex items-center justify-between gap-3 px-4 py-3">
              <div className="min-w-0">
                <div className="truncate text-sm text-ink">{u.display_name || u.email}</div>
                {u.display_name ? (
                  <div className="truncate text-xs text-faint">{u.email}</div>
                ) : null}
              </div>
              <button
                className="btn shrink-0"
                type="button"
                aria-label={picked ? `Deselect ${u.email}` : `Add ${u.email}`}
                onClick={() => toggle(u.id)}
                disabled={busy}
              >
                <FontAwesomeIcon icon={picked ? faCheck : faUserPlus} />
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
          className="btn"
          type="button"
          onClick={() => void done()}
          disabled={busy}
          title={
            busy ? "Adding…" : selected.size ? `Done (${selected.size})` : "Done"
          }
          aria-label={
            busy ? "Adding…" : selected.size ? `Done (${selected.size})` : "Done"
          }
        >
          <FontAwesomeIcon icon={faCheck} />
        </button>
      </div>
    </Modal>
  );
}
