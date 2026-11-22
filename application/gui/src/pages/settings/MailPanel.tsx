import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faEnvelope, faFloppyDisk, faPaperPlane } from "@fortawesome/free-solid-svg-icons";
import { useEffect, useState } from "react";
import { api } from "../../api";
import { ErrorNote } from "../../components/page";
import { useApiResource } from "../../hooks/useAsyncResource";
import { errorMessage } from "../../errors";

type MailSettings = {
  host: string;
  port: number;
  username: string;
  from_address: string;
  use_tls: boolean;
  configured: boolean;
  password_set: boolean;
};

export function MailPanel() {
  const [busy, setBusy] = useState(false);
  const [testBusy, setTestBusy] = useState(false);
  const [savedNote, setSavedNote] = useState<string | null>(null);
  const { data, error, setData, setError } = useApiResource<MailSettings>("/api/system/mail");

  const [host, setHost] = useState("");
  const [port, setPort] = useState("587");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [clearPassword, setClearPassword] = useState(false);
  const [fromAddress, setFromAddress] = useState("");
  const [useTls, setUseTls] = useState(true);

  useEffect(() => {
    if (!data) return;
    setHost(data.host);
    setPort(String(data.port));
    setUsername(data.username);
    setFromAddress(data.from_address);
    setUseTls(data.use_tls);
    setPassword("");
    setClearPassword(false);
  }, [data]);

  async function save() {
    setBusy(true);
    setSavedNote(null);
    try {
      const portNum = Number.parseInt(port, 10);
      if (!Number.isFinite(portNum) || portNum < 1 || portNum > 65535) {
        throw new Error("Port must be between 1 and 65535");
      }
      const body: Record<string, unknown> = {
        host,
        port: portNum,
        username,
        from_address: fromAddress,
        use_tls: useTls,
        clear_password: clearPassword,
      };
      if (!clearPassword && password) {
        body.password = password;
      }
      const row = await api<MailSettings>("/api/system/mail", {
        method: "PUT",
        body: JSON.stringify(body),
      });
      setData(row);
      setError(null);
      setPassword("");
      setClearPassword(false);
      setSavedNote(row.configured ? "Saved. Outbound mail is enabled." : "Saved. Outbound mail is off.");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function sendTest() {
    setTestBusy(true);
    setSavedNote(null);
    try {
      const row = await api<{ status: string; to: string }>("/api/system/mail/test", {
        method: "POST",
      });
      setError(null);
      setSavedNote(`Test message sent to ${row.to}.`);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setTestBusy(false);
    }
  }

  const configured = data?.configured === true;

  return (
    <div className="page-body">
      <ErrorNote error={error} />
      <div className="mb-3">
        <h3 className="text-sm font-medium text-ink">Outbound mail (SMTP)</h3>
        <p className="text-xs text-muted mt-0.5">
          When a host and From address are set, memchentic sends group invitations,
          registration confirmation links, and API-key change notices. With mail
          off, registration works as before (accounts are usable immediately) and
          no messages are sent.{" "}
          <code className="text-ink">SMTP_*</code> environment variables only set
          the values on the first boot; after that, this panel is kept across
          restarts.
        </p>
        <p className="text-xs mt-2" role="status">
          <FontAwesomeIcon icon={faEnvelope} className="opacity-70 mr-1.5" />
          {data == null
            ? "Loading…"
            : configured
              ? "Mail is configured."
              : "Mail is not configured."}
          {data?.password_set ? " Password is set." : null}
        </p>
        {savedNote ? <p className="text-xs text-muted mt-1">{savedNote}</p> : null}
      </div>

      <div className="flex flex-col gap-3 max-w-md">
        <label className="auth-field">
          <span>SMTP host</span>
          <input
            className="input"
            value={host}
            onChange={(e) => setHost(e.target.value)}
            placeholder="smtp.example.com"
            autoComplete="off"
            disabled={data == null || busy}
          />
        </label>
        <label className="auth-field">
          <span>Port</span>
          <input
            className="input"
            value={port}
            onChange={(e) => setPort(e.target.value)}
            inputMode="numeric"
            disabled={data == null || busy}
          />
        </label>
        <label className="auth-field">
          <span>Username</span>
          <input
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="off"
            disabled={data == null || busy}
          />
        </label>
        <label className="auth-field">
          <span>Password</span>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              if (e.target.value) setClearPassword(false);
            }}
            placeholder={data?.password_set ? "Leave blank to keep current" : "Optional"}
            autoComplete="new-password"
            disabled={data == null || busy || clearPassword}
          />
        </label>
        <label className="flex items-center gap-2.5 text-sm font-medium text-ink">
          <input
            type="checkbox"
            className="accent-current"
            checked={clearPassword}
            onChange={(e) => {
              setClearPassword(e.target.checked);
              if (e.target.checked) setPassword("");
            }}
            disabled={data == null || busy}
          />
          Clear stored password
        </label>
        <label className="auth-field">
          <span>From address</span>
          <input
            className="input"
            type="email"
            value={fromAddress}
            onChange={(e) => setFromAddress(e.target.value)}
            placeholder="noreply@example.com"
            autoComplete="off"
            disabled={data == null || busy}
          />
        </label>
        <label className="flex items-center gap-2.5 text-sm font-medium text-ink">
          <input
            type="checkbox"
            className="accent-current"
            checked={useTls}
            onChange={(e) => setUseTls(e.target.checked)}
            disabled={data == null || busy}
          />
          Use STARTTLS
        </label>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn" disabled={data == null || busy} onClick={() => void save()}>
            <FontAwesomeIcon icon={faFloppyDisk} />
            Save
          </button>
          <button
            type="button"
            className="btn"
            disabled={data == null || !configured || testBusy || busy}
            onClick={() => void sendTest()}
          >
            <FontAwesomeIcon icon={faPaperPlane} />
            Send test
          </button>
        </div>
      </div>
    </div>
  );
}
