import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faCopy, faKey, faTrash } from "@fortawesome/free-solid-svg-icons";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, PageHeader } from "../components/ui";

type Key = { id: string; name: string; prefix: string; created_at: string | null; user_id: string };

export default function ApiKeysPage() {
  const { me } = useAuth();
  const [keys, setKeys] = useState<Key[]>([]);
  const [name, setName] = useState("");
  const [secret, setSecret] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mcpUrl, setMcpUrl] = useState("");

  const load = useCallback(async () => {
    try {
      setKeys(await api<Key[]>("/api/api-keys"));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void load();
    api<{ mcp_url: string }>("/api/provider").then((p) => setMcpUrl(p.mcp_url)).catch(() => undefined);
  }, [load]);

  async function create(e: FormEvent) {
    e.preventDefault();
    try {
      const res = await api<{ key: string }>("/api/api-keys", { method: "POST", body: JSON.stringify({ name }) });
      setSecret(res.key);
      setName("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function revoke(id: string) {
    if (!confirm("Revoke this key? Harnesses using it stop working.")) return;
    try {
      await api(`/api/api-keys/${id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div>
      <PageHeader title="API keys" />
      <div className="page-body">
        <p className="mb-4 text-sm text-muted">
          Keys act as you over MCP and HTTP: your personal space plus every team you are on.
          {mcpUrl && (
            <>
              {" "}
              Point your harness at <code className="text-ink">{mcpUrl}</code> with{" "}
              <code className="text-ink">Authorization: Bearer &lt;key&gt;</code>.
            </>
          )}
        </p>
        {me?.is_admin && !me.id && (
          <p className="mb-4 text-xs text-faint">
            The env admin uses MEMCHENTIC_ADMIN_API_KEY; per-user keys belong to user accounts.
          </p>
        )}
        <ErrorNote error={error} />
        {me?.id && (
          <form className="mb-6 flex gap-2" onSubmit={create}>
            <input
              className="input max-w-sm"
              placeholder="Key name (e.g. cursor-laptop)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <button className="btn" type="submit">
              <FontAwesomeIcon icon={faKey} /> Issue key
            </button>
          </form>
        )}
        {secret && (
          <div className="panel mb-6 p-4">
            <p className="mb-2 text-sm text-muted">Copy this key now. It is not shown again.</p>
            <div className="flex items-center gap-2">
              <code className="flex-1 break-all border border-border bg-surface px-3 py-2 text-sm text-ink">
                {secret}
              </code>
              <button className="btn" type="button" onClick={() => void navigator.clipboard?.writeText(secret)}>
                <FontAwesomeIcon icon={faCopy} /> Copy
              </button>
              <button className="btn" type="button" onClick={() => setSecret(null)}>
                Done
              </button>
            </div>
          </div>
        )}
        <ul className="list-stack">
          {keys.map((k) => (
            <li key={k.id} className="list-row flex items-center justify-between text-sm">
              <span>
                <span className="text-ink">{k.name}</span>
                <code className="ml-3 text-faint">{k.prefix}…</code>
                <span className="ml-3 text-xs text-faint">
                  {k.created_at ? new Date(k.created_at).toLocaleString() : ""}
                </span>
              </span>
              <button className="btn" type="button" onClick={() => void revoke(k.id)}>
                <FontAwesomeIcon icon={faTrash} /> Revoke
              </button>
            </li>
          ))}
          {!keys.length && <p className="text-sm text-faint">No active keys.</p>}
        </ul>
      </div>
    </div>
  );
}
