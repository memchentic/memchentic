import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { formatDocumentTitle } from "../documentTitle";
import { errorMessage } from "../errors";

export default function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get("token")?.trim() || "";
  const [status, setStatus] = useState<"working" | "ok" | "error">(
    token ? "working" : "error",
  );
  const [message, setMessage] = useState(
    token ? "Confirming your email…" : "Missing confirmation token.",
  );

  useEffect(() => {
    document.title = formatDocumentTitle("Confirm email");
  }, []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const row = await api<{ status: string; email: string }>("/api/auth/verify-email", {
          method: "POST",
          body: JSON.stringify({ token }),
        });
        if (cancelled) return;
        setStatus("ok");
        setMessage(`Confirmed ${row.email}. You can sign in now.`);
      } catch (err) {
        if (cancelled) return;
        setStatus("error");
        setMessage(errorMessage(err));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  return (
    <main className="auth-shell">
      <section className="auth-card">
        <header className="auth-header">
          <h1>Confirm email</h1>
          <p>{message}</p>
        </header>
        {status !== "working" ? (
          <p className="auth-switch">
            <Link className="auth-switch-action" to="/sign-in">
              Sign in
            </Link>
          </p>
        ) : null}
      </section>
    </main>
  );
}
