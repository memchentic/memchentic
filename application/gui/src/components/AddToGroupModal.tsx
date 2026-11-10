import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faPlus, faXmark } from "@fortawesome/free-solid-svg-icons";
import { useMemo, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorNote } from "./page";
import { Modal } from "./overlay";
import { useGroupDetails } from "../hooks/useGroupDetails";
import { errorMessage } from "../errors";

export function AddToGroupModal({
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
