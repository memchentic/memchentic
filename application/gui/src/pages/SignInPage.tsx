import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faGithub,
  faGitlab,
  faGoogle,
  faMicrosoft,
  faOpenid,
} from "@fortawesome/free-brands-svg-icons";
import {
  faArrowRightToBracket,
  faKey,
  faShieldHalved,
  faUserPlus,
} from "@fortawesome/free-solid-svg-icons";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useLocation, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { safeReturnPath } from "../returnPath";
import { formatDocumentTitle } from "../documentTitle";
import { oauthErrorMessage } from "../oauthErrors";

type Provider = {
  oauth_provider: string;
  base_url: string;
  mcp_url: string;
  web_oauth?: boolean;
  oauth_label?: string;
  register_enabled?: boolean;
};

function providerIcon(name: string): IconDefinition {
  switch (name) {
    case "google":
      return faGoogle;
    case "github":
      return faGithub;
    case "gitlab":
      return faGitlab;
    case "entra":
      return faMicrosoft;
    case "generic":
      return faOpenid;
    case "okta":
    case "authentik":
    case "keycloak":
      return faShieldHalved;
    case "auth0":
      return faKey;
    default:
      return faOpenid;
  }
}

export default function SignInPage() {
  const { me, login, register } = useAuth();
  const [searchParams] = useSearchParams();
  const location = useLocation();
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

  useEffect(() => {
    const message = oauthErrorMessage(searchParams.get("error"));
    if (message) setError(message);
  }, [searchParams]);

  useEffect(() => {
    if (mode === "register" && provider && provider.register_enabled === false) {
      setMode("login");
    }
  }, [mode, provider]);

  useEffect(() => {
    document.title = formatDocumentTitle(
      mode === "login" || provider?.register_enabled === false ? "Sign in" : "Create account",
    );
  }, [mode, provider]);

  if (me) return <Navigate to={safeReturnPath(location.state)} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "login" || !registerEnabled) await login(email, password, remember);
      else await register(email, password, displayName || undefined, remember);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const internal = !provider || provider.oauth_provider === "internal";
  // Offer registration only after /api/provider has confirmed it is enabled.
  const registerEnabled = provider?.register_enabled === true;
  const webOauth = Boolean(provider?.web_oauth) && mode === "login";
  const oauthName = provider?.oauth_provider || "generic";
  const oauthLabel = provider?.oauth_label || oauthName;
  const effectiveMode = mode === "register" && !registerEnabled ? "login" : mode;
  const title = effectiveMode === "login" ? "Sign in" : "Create account";
  const subtitle =
    effectiveMode === "login"
      ? "Sign in with your email and password."
      : "Email is required — it is your account identity on this instance.";

  function continueWithProvider() {
    const params = remember ? "?remember=true" : "";
    window.location.assign(`/api/auth/oauth/start${params}`);
  }

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
          {effectiveMode === "register" && (
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
              minLength={effectiveMode === "register" ? 8 : undefined}
              autoComplete={
                effectiveMode === "login" ? "current-password" : "new-password"
              }
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
            <FontAwesomeIcon
              icon={effectiveMode === "login" ? faArrowRightToBracket : faUserPlus}
            />
            {effectiveMode === "login" ? "Sign in" : "Create account"}
          </button>
        </form>

        {internal && registerEnabled && (
          <p className="auth-switch">
            {effectiveMode === "login" ? "No account? " : "Have an account? "}
            <button
              className="auth-switch-action"
              type="button"
              onClick={() =>
                setMode(effectiveMode === "login" ? "register" : "login")
              }
            >
              {effectiveMode === "login" ? "Register" : "Sign in"}
            </button>
          </p>
        )}

        {webOauth ? (
          <div className="auth-sso">
            <div className="auth-divider" role="separator">
              <span>or</span>
            </div>
            <button className="btn w-full" type="button" onClick={continueWithProvider}>
              <FontAwesomeIcon icon={providerIcon(oauthName)} />
              Continue with {oauthLabel}
            </button>
          </div>
        ) : null}
      </section>
    </main>
  );
}
