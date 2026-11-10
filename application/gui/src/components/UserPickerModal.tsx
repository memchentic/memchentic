import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";
import { faCheck, faXmark } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { DirectoryUser } from "../apiTypes";
import { errorMessage } from "../errors";
import { ErrorNote } from "./page";
import { Modal } from "./overlay";

/**
 * Dialog that lists directory users, lets the caller select several, and
 * adds them one request at a time through `addUser`. With `inviteByEmail`
 * it also offers to invite an email address that is not in the list.
 */
export function UserPickerModal({
  open,
  titleId,
  excludeIds,
  addIcon,
  note,
  addUser,
  inviteByEmail,
  onAdded,
  onClose,
}: {
  open: boolean;
  /** DOM id of the heading; must be unique on the page. */
  titleId: string;
  /** Users already on the roster; they are not listed. */
  excludeIds: ReadonlySet<string>;
  /** Icon on a user's button while that user is not selected. */
  addIcon: IconDefinition;
  /** Text shown under the search field. */
  note?: string;
  /** Adds one user. A rejection stops the batch and is shown in the dialog. */
  addUser: (userId: string) => Promise<unknown>;
  /** Invites one email address. Leave out to offer no invitation by email. */
  inviteByEmail?: (email: string) => Promise<unknown>;
  /** Called once after a batch in which at least one user was added, and after an invitation. */
  onAdded: () => void;
  onClose: () => void;
}) {
  const [users, setUsers] = useState<DirectoryUser[]>([]);
  const [lookupHit, setLookupHit] = useState<DirectoryUser | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLookupHit(null);
    setQuery("");
    setSelected(new Set());
    setError(null);
    setNotice(null);
    setBusy(false);
    api<DirectoryUser[]>("/api/users")
      .then(setUsers)
      .catch((err) => setError(errorMessage(err)));
  }, [open]);

  // An exact email that is not in the loaded directory is looked up on its own.
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
      .filter((u) => !excludeIds.has(u.id))
      .filter((u) => {
        if (!q) return true;
        return (
          u.email.toLowerCase().includes(q) ||
          (u.display_name ?? "").toLowerCase().includes(q)
        );
      });
    if (
      lookupHit &&
      !excludeIds.has(lookupHit.id) &&
      !rows.some((u) => u.id === lookupHit.id)
    ) {
      return [...rows, lookupHit];
    }
    return rows;
  }, [users, excludeIds, query, lookupHit]);

  function toggle(userId: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(userId)) next.delete(userId);
      else next.add(userId);
      return next;
    });
  }

  const emailQuery = query.trim();
  const canInviteByEmail =
    inviteByEmail !== undefined &&
    emailQuery.includes("@") &&
    !emailQuery.includes(" ") &&
    !filtered.some((u) => u.email.toLowerCase() === emailQuery.toLowerCase());

  async function sendInvite() {
    if (!inviteByEmail) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await inviteByEmail(emailQuery);
      setNotice(`If ${emailQuery} has an account, an invitation is waiting for them.`);
      setQuery("");
      onAdded();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
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
        await addUser(userId);
        added += 1;
        // Drop the user from the selection so a retry after a later failure
        // does not add them again.
        setSelected((prev) => {
          const next = new Set(prev);
          next.delete(userId);
          return next;
        });
      }
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
      // Refresh the roster even after a partial failure.
      if (added > 0) onAdded();
    }
  }

  const doneLabel = busy ? "Adding…" : selected.size ? `Done (${selected.size})` : "Done";

  return (
    <Modal open={open} onClose={onClose} labelledBy={titleId} closeDisabled={busy}>
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <h3 id={titleId} className="font-medium text-ink">
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
          aria-label="Search users"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoFocus
        />
      </div>
      {note ? <p className="px-4 pt-3 text-xs text-muted">{note}</p> : null}
      {error ? (
        <div className="px-4 pt-3">
          <ErrorNote error={error} />
        </div>
      ) : null}
      <ul className="min-h-0 flex-1 overflow-y-auto divide-y divide-border">
        {notice ? <li className="px-4 py-3 text-sm text-muted">{notice}</li> : null}
        {!filtered.length && !canInviteByEmail ? (
          <li className="px-4 py-6 text-center text-sm text-faint">No users to add.</li>
        ) : null}
        {canInviteByEmail ? (
          <li className="flex items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0 truncate text-sm text-ink">Invite {emailQuery}</div>
            <button
              className="btn shrink-0"
              type="button"
              aria-label={`Invite ${emailQuery}`}
              onClick={() => void sendInvite()}
              disabled={busy}
            >
              <FontAwesomeIcon icon={addIcon} />
            </button>
          </li>
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
                <FontAwesomeIcon icon={picked ? faCheck : addIcon} />
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
          title={doneLabel}
          aria-label={doneLabel}
        >
          <FontAwesomeIcon icon={faCheck} />
        </button>
      </div>
    </Modal>
  );
}
