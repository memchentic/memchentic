import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faFloppyDisk, faLink } from "@fortawesome/free-solid-svg-icons";
import { useEffect, useState } from "react";
import { api } from "../../api";
import { ErrorNote } from "../../components/page";
import { useApiResource } from "../../hooks/useAsyncResource";
import { errorMessage } from "../../errors";

type WebhookSettings = {
  enabled: boolean;
  url: string;
  secret_set: boolean;
  fail_count: number;
};

export function WebhooksPanel() {
  const [busy, setBusy] = useState(false);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const { data, error, setData, setError } = useApiResource<WebhookSettings>(
    "/api/system/webhooks",
  );

  const [enabled, setEnabled] = useState(false);
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [clearSecret, setClearSecret] = useState(false);

  useEffect(() => {
    if (!data) return;
    setEnabled(data.enabled);
    setUrl(data.url);
    setSecret("");
    setClearSecret(false);
  }, [data]);

  async function save() {
    setBusy(true);
    setSavedNote(null);
    try {
      const body: Record<string, unknown> = {
        enabled,
        url,
        clear_secret: clearSecret,
      };
      if (!clearSecret && secret) {
        body.secret = secret;
      }
      const row = await api<WebhookSettings>("/api/system/webhooks", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      setData(row);
      setError(null);
      setSecret("");
      setClearSecret(false);
      setSavedNote(
        row.enabled ? "Saved. Webhooks are enabled." : "Saved. Webhooks are off.",
      );
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page-body">
      <ErrorNote error={error} />
      <div className="mb-3">
        <h3 className="text-sm font-medium text-ink flex items-center gap-2">
          <FontAwesomeIcon icon={faLink} className="opacity-70" />
          Mutation webhooks
        </h3>
        <p className="text-xs text-muted mt-0.5">
          POST memory create/update/delete events to an HTTPS URL for any
          downstream agent runtime. The secret (when set) is sent as an HMAC
          signature header and is never returned.
        </p>
      </div>

      <div className="panel grid gap-3 p-4 max-w-xl">
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={enabled}
            disabled={busy || data === null}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          Enabled
        </label>
        <label className="grid gap-1 text-sm">
          <span className="text-muted">HTTPS URL</span>
          <input
            className="input"
            value={url}
            disabled={busy || data === null}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com/hooks/memchentic"
            autoComplete="off"
          />
        </label>
        <label className="grid gap-1 text-sm">
          <span className="text-muted">
            Secret{data?.secret_set ? " (set — leave blank to keep)" : ""}
          </span>
          <input
            className="input"
            type="password"
            value={secret}
            disabled={busy || data === null || clearSecret}
            onChange={(e) => setSecret(e.target.value)}
            autoComplete="new-password"
          />
        </label>
        <label className="flex items-center gap-2 text-sm text-ink">
          <input
            type="checkbox"
            checked={clearSecret}
            disabled={busy || data === null}
            onChange={(e) => setClearSecret(e.target.checked)}
          />
          Clear secret
        </label>
        {data != null && data.fail_count > 0 ? (
          <p className="text-xs text-muted">
            Recent delivery failures: {data.fail_count}
          </p>
        ) : null}
        <div className="flex justify-end">
          <button
            className="btn"
            type="button"
            disabled={busy || data === null}
            title="Save"
            aria-label="Save"
            onClick={() => void save()}
          >
            <FontAwesomeIcon icon={faFloppyDisk} />
          </button>
        </div>
        {savedNote ? <p className="text-xs text-muted">{savedNote}</p> : null}
      </div>
    </div>
  );
}
