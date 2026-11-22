import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faToggleOff, faToggleOn, faXmark } from "@fortawesome/free-solid-svg-icons";
import { useId, useState } from "react";
import { api } from "../../api";
import { ErrorNote } from "../../components/page";
import { Modal } from "../../components/overlay";
import { useApiResource } from "../../hooks/useAsyncResource";
import { errorMessage } from "../../errors";

type AutomationSettings = {
  resolve_supersedes: boolean;
  resolve_near_dups: boolean;
};

type EnablePrompt = {
  field: keyof AutomationSettings;
  label: string;
  clearCopy: string;
};

const ENABLE_PROMPTS: Record<keyof AutomationSettings, Omit<EnablePrompt, "field">> = {
  resolve_supersedes: {
    label: "Auto-resolve conflicts",
    clearCopy:
      "Also forget any pending superseded memories right now? Later passes still run on the schedule.",
  },
  resolve_near_dups: {
    label: "Auto-resolve near-duplicates",
    clearCopy:
      "Also forget the older memory in each pending near-duplicate pair right now? Later passes still run on the schedule.",
  },
};

export function AutomationPanel() {
  const [busy, setBusy] = useState(false);
  const [prompt, setPrompt] = useState<EnablePrompt | null>(null);
  const titleId = useId();
  const { data, error, setData, setError } = useApiResource<AutomationSettings>(
    "/api/system/automation",
  );

  async function patch(next: Partial<AutomationSettings>) {
    setBusy(true);
    try {
      const row = await api<AutomationSettings>("/api/system/automation", {
        method: "PUT",
        body: JSON.stringify(next),
      });
      setData(row);
      setError(null);
      return row;
    } catch (err) {
      setError(errorMessage(err));
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function runNow() {
    await api<{ conflicts_forgotten: number; near_dups_forgotten: number }>(
      "/api/system/automation/run",
      { method: "POST" },
    );
  }

  function requestEnable(field: keyof AutomationSettings) {
    if (data?.[field] === true) return;
    const copy = ENABLE_PROMPTS[field];
    setPrompt({ field, ...copy });
  }

  async function confirmEnable(clearPending: boolean) {
    if (!prompt) return;
    const field = prompt.field;
    setPrompt(null);
    const row = await patch({ [field]: true });
    if (!row || !clearPending) return;
    setBusy(true);
    try {
      await runNow();
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-body">
      <ErrorNote error={error} />
      <div className="mb-6">
        <h3 className="text-sm font-medium text-ink">Hygiene automation</h3>
        <p className="text-xs text-muted mt-0.5">
          When enabled, the instance periodically deletes rows that Hygiene would
          otherwise list: superseded targets of a live conflict, and (optionally)
          the older memory in each near-duplicate pair. Both toggles default off.
          Manual Resolve / Keep actions on the Hygiene page always work.
        </p>
      </div>

      <div className="mb-6">
        <h4 className="text-sm font-medium text-ink mb-2">Auto-resolve conflicts</h4>
        <p className="text-xs text-muted mb-2">
          Forget the superseded memory when a replacement declares{" "}
          <code className="text-ink">supersedes</code>. Search already hides those
          targets.
        </p>
        <TogglePair
          value={data?.resolve_supersedes ?? null}
          busy={busy || data === null}
          onEnable={() => requestEnable("resolve_supersedes")}
          onDisable={() => void patch({ resolve_supersedes: false })}
          label="Auto-resolve conflicts"
        />
      </div>

      <div className="mb-3">
        <h4 className="text-sm font-medium text-ink mb-2">Auto-resolve near-duplicates</h4>
        <p className="text-xs text-muted mb-2">
          For each near-duplicate pair, keep the newer memory by{" "}
          <code className="text-ink">updated_at</code> and forget the older one.
        </p>
        <TogglePair
          value={data?.resolve_near_dups ?? null}
          busy={busy || data === null}
          onEnable={() => requestEnable("resolve_near_dups")}
          onDisable={() => void patch({ resolve_near_dups: false })}
          label="Auto-resolve near-duplicates"
        />
      </div>

      <Modal
        open={prompt !== null}
        onClose={() => setPrompt(null)}
        labelledBy={titleId}
        closeDisabled={busy}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h3 id={titleId} className="font-medium text-ink">
            {prompt?.label ?? "Enable automation"}
          </h3>
          <button
            className="btn"
            type="button"
            onClick={() => setPrompt(null)}
            disabled={busy}
            aria-label="Close"
          >
            <FontAwesomeIcon icon={faXmark} />
          </button>
        </div>
        <div className="px-4 py-4 text-sm text-muted">{prompt?.clearCopy}</div>
        <div className="flex flex-wrap justify-end gap-2 border-t border-border px-4 py-3">
          <button
            className="btn"
            type="button"
            disabled={busy}
            onClick={() => void confirmEnable(false)}
          >
            Enable only
          </button>
          <button
            className="btn"
            type="button"
            disabled={busy}
            onClick={() => void confirmEnable(true)}
          >
            Enable and clear now
          </button>
        </div>
      </Modal>
    </div>
  );
}

function TogglePair({
  value,
  busy,
  onEnable,
  onDisable,
  label,
}: {
  value: boolean | null;
  busy: boolean;
  onEnable: () => void;
  onDisable: () => void;
  label: string;
}) {
  return (
    <div className="settings-group-picker" role="group" aria-label={label}>
      <button
        type="button"
        className={`settings-group-chip ${value === true ? "settings-group-chip-active" : ""}`}
        aria-pressed={value === true}
        disabled={busy}
        onClick={onEnable}
      >
        <FontAwesomeIcon icon={faToggleOn} className="opacity-70" />
        <span>Enabled</span>
      </button>
      <button
        type="button"
        className={`settings-group-chip ${value === false ? "settings-group-chip-active" : ""}`}
        aria-pressed={value === false}
        disabled={busy}
        onClick={onDisable}
      >
        <FontAwesomeIcon icon={faToggleOff} className="opacity-70" />
        <span>Disabled</span>
      </button>
    </div>
  );
}
