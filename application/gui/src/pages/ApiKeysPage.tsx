import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faCheck, faCopy, faRotate } from "@fortawesome/free-solid-svg-icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { copyText } from "../clipboard";
import { ErrorNote, PageHeader } from "../components/page";
import type { DirectoryUser } from "../apiTypes";
import { formatDateTime } from "../format";
import { errorMessage } from "../errors";

type Key = {
  id: string;
  prefix: string;
  created_at: string | null;
  user_id: string;
};

type CycleResult = { key: string; prefix: string; user_id: string };

export default function ApiKeysPage() {
  const { me } = useAuth();
  const [keys, setKeys] = useState<Key[]>([]);
  const [users, setUsers] = useState<DirectoryUser[]>([]);
  const [secret, setSecret] = useState<CycleResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyUserId, setBusyUserId] = useState<string | null>(null);
  const [mcpUrl, setMcpUrl] = useState("");
  // False until the first load attempt has finished.
  const [loaded, setLoaded] = useState(false);
  const isAdmin = Boolean(me?.is_admin);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const secretInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    // A new secret (or dismissing it) resets the copy feedback.
    setCopyState("idle");
  }, [secret]);

  async function copySecret(text: string) {
    if (await copyText(text)) {
      setCopyState("copied");
      return;
    }
    setCopyState("failed");
    secretInputRef.current?.focus();
    secretInputRef.current?.select();
  }

  const load = useCallback(async () => {
    try {
      const nextKeys = await api<Key[]>("/api/api-keys");
      setKeys(nextKeys);
      if (isAdmin) {
        setUsers(await api<DirectoryUser[]>("/api/users"));
      }
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoaded(true);
    }
  }, [isAdmin]);

  useEffect(() => {
    void load();
    api<{ mcp_url: string }>("/api/provider")
      .then((p) => setMcpUrl(p.mcp_url))
      .catch(() => undefined);
  }, [load]);

  const keysByUser = useMemo(() => {
    const map = new Map<string, Key>();
    for (const key of keys) {
      const prior = map.get(key.user_id);
      if (!prior) {
        map.set(key.user_id, key);
        continue;
      }
      const priorTs = prior.created_at ? Date.parse(prior.created_at) : 0;
      const nextTs = key.created_at ? Date.parse(key.created_at) : 0;
      if (nextTs >= priorTs) map.set(key.user_id, key);
    }
    return map;
  }, [keys]);

  const ownKey = me?.id ? keysByUser.get(me.id) : undefined;

  async function cycle(userId?: string) {
    const target = userId ?? me?.id ?? undefined;
    if (!target) {
      setError("Sign in with a user account to cycle an API key.");
      return;
    }
    const label = userId && userId !== me?.id ? "this user's" : "your";
    if (
      !confirm(
        `Cycle ${label} API key? The current secret stops working and a new one is shown once.`,
      )
    ) {
      return;
    }
    setBusyUserId(target);
    try {
      const res = await api<CycleResult>("/api/api-keys/cycle", {
        method: "POST",
        body: JSON.stringify(userId ? { user_id: userId } : {}),
      });
      setSecret(res);
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusyUserId(null);
    }
  }

  return (
    <div>
      <PageHeader title="API keys" />
      <div className="page-body">
        <p className="mb-4 text-sm text-muted">
          Keys act as you over MCP and HTTP: your personal space plus every group you are on.
          {mcpUrl && (
            <>
              {" "}
              Point your harness at <code className="text-ink">{mcpUrl}</code> with{" "}
              <code className="text-ink">Authorization: Bearer &lt;key&gt;</code>.
            </>
          )}
        </p>
        {isAdmin && !me?.id && (
          <p className="mb-4 text-xs text-faint">
            Env admin has no personal key. Cycle keys for directory users below.
          </p>
        )}
        <ErrorNote error={error} />

        {secret ? (
          <div className="panel reveal-fade mb-6 p-4">
            <p className="mb-2 text-sm text-muted">
              Copy this key now. It is not shown again
              {secret.user_id !== me?.id ? " (issued for another user)" : ""}.
            </p>
            <div className="flex items-center gap-2">
              <input
                ref={secretInputRef}
                readOnly
                className="input flex-1 font-mono text-sm"
                value={secret.key}
                aria-label="New API key"
                onFocus={(e) => e.currentTarget.select()}
              />
              <button
                className="btn"
                type="button"
                title="Copy"
                aria-label="Copy"
                onClick={() => void copySecret(secret.key)}
              >
                <FontAwesomeIcon icon={faCopy} />
              </button>
              <button
                className="btn"
                type="button"
                title="Done"
                aria-label="Done"
                onClick={() => setSecret(null)}
              >
                <FontAwesomeIcon icon={faCheck} />
              </button>
            </div>
            <p className="mt-2 text-xs text-faint" role="status">
              {copyState === "copied"
                ? "Copied to the clipboard."
                : copyState === "failed"
                  ? "Copy failed. The key is selected: press Ctrl+C (Cmd+C on macOS)."
                  : ""}
            </p>
          </div>
        ) : null}

        {!isAdmin && (
          <section className="panel mb-6 p-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="text-sm text-ink">Your API key</p>
                {ownKey ? (
                  <p className="mt-1 text-sm text-muted">
                    <code className="text-ink">{ownKey.prefix}…</code>
                    <span className="ml-3 text-xs text-faint">
                      {ownKey.created_at
                        ? `issued ${formatDateTime(ownKey.created_at)}`
                        : ""}
                    </span>
                  </p>
                ) : (
                  <p className="mt-1 text-sm text-faint">
                    {loaded ? "No active key yet." : "Loading…"}
                  </p>
                )}
              </div>
              <button
                className="btn"
                type="button"
                title={ownKey ? "Cycle key" : "Generate key"}
                aria-label={ownKey ? "Cycle key" : "Generate key"}
                disabled={!me?.id || busyUserId === me?.id}
                onClick={() => void cycle()}
              >
                <FontAwesomeIcon icon={faRotate} />
              </button>
            </div>
          </section>
        )}

        {isAdmin && (
          <section>
            <h3 className="mb-3 text-sm font-medium text-ink">All users</h3>
            <div className="panel overflow-x-auto">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Key</th>
                    <th>Issued</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => {
                    const key = keysByUser.get(u.id);
                    return (
                      <tr key={u.id}>
                        <td>
                          <div className="text-ink">{u.email}</div>
                          <div className="text-xs text-faint">
                            {u.display_name ?? "—"}
                            {u.id === me?.id ? " · you" : ""}
                          </div>
                        </td>
                        <td className="text-muted">
                          {key ? <code>{key.prefix}…</code> : "—"}
                        </td>
                        <td className="text-faint">
                          {formatDateTime(key?.created_at)}
                        </td>
                        <td className="text-right">
                          <button
                            className="btn"
                            type="button"
                            title={key ? "Cycle" : "Generate"}
                            aria-label={key ? "Cycle" : "Generate"}
                            disabled={busyUserId === u.id}
                            onClick={() => void cycle(u.id)}
                          >
                            <FontAwesomeIcon icon={faRotate} />
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                  {!users.length && (
                    <tr>
                      <td className="text-sm text-faint" colSpan={4}>
                        {loaded ? "No users yet." : "Loading…"}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
