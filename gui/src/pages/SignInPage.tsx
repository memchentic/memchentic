import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faArrowRightToBracket, faUserPlus } from "@fortawesome/free-solid-svg-icons";
import { useEffect, useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";

type Provider = { oauth_provider: string; base_url: string; mcp_url: string };

export default function SignInPage() {
  const { me, login, register } = useAuth();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [provider, setProvider] = useState<Provider | null>(null);

  useEffect(() => {
    api<Provider>("/api/provider").then(setProvider).catch(() => setProvider(null));
  }, []);

  if (me) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login") await login(email, password, remember);
      else await register(email, password, displayName || undefined, remember);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const internal = !provider || provider.oauth_provider === "internal";
  const title = mode === "login" ? "Sign in" : "Create account";
  const subtitle =
    mode === "login"
      ? "Sign in with your email (or the admin email) and password."
      : "Email is required — it is your account identity on this instance.";

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <header className="auth-header">
          <h1>{title}</h1>
          <p>{subtitle}</p>
        </header>

        {error ? (
          <div className="flash-error" role="alert">
            {error}
          </div>
        ) : null}

        <form className="auth-form" onSubmit={submit}>
          <label className="auth-field">
            <span>Email</span>
            <input
              className="input"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="username"
              autoFocus
              placeholder="you@example.com"
            />
          </label>
          {mode === "register" && (
            <label className="auth-field">
              <span>Display name</span>
              <input
                className="input"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
              />
            </label>
          )}
          <label className="auth-field">
            <span>Password</span>
            <input
              className="input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={mode === "register" ? 8 : undefined}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
            />
          </label>
          <label className="flex items-center gap-2.5 text-sm font-medium text-ink">
            <input
              type="checkbox"
              className="accent-current"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
            />
            Remember this session
          </label>
          <button className="btn w-full" disabled={busy} type="submit">
            <FontAwesomeIcon icon={mode === "login" ? faArrowRightToBracket : faUserPlus} />
            {mode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>

        {internal && (
          <p className="auth-switch">
            {mode === "login" ? "No account? " : "Have an account? "}
            <button
              className="auth-switch-action"
              type="button"
              onClick={() => setMode(mode === "login" ? "register" : "login")}
            >
              {mode === "login" ? "Register" : "Sign in"}
            </button>
          </p>
        )}

        {provider && (
          <p className="auth-meta">
            Provider: <span className="text-muted">{provider.oauth_provider}</span>
            <br />
            MCP endpoint: <span className="break-all text-muted">{provider.mcp_url}</span>
          </p>
        )}
      </section>
    </main>
  );
}
