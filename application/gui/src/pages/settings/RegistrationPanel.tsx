import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faToggleOff, faToggleOn } from "@fortawesome/free-solid-svg-icons";
import { useState } from "react";
import { api } from "../../api";
import { ErrorNote } from "../../components/page";
import { useApiResource } from "../../hooks/useAsyncResource";
import { errorMessage } from "../../errors";

export function RegistrationPanel() {
  const [busy, setBusy] = useState(false);
  const { data, error, setData, setError } = useApiResource<{ enabled: boolean }>(
    "/api/system/registration",
  );
  const enabled = data ? data.enabled : null;

  async function setRegistration(next: boolean) {
    setBusy(true);
    try {
      const row = await api<{ enabled: boolean }>("/api/system/registration", {
        method: "PUT",
        body: JSON.stringify({ enabled: next }),
      });
      setData(row);
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
      <div className="mb-3">
        <h3 className="text-sm font-medium text-ink">Public registration</h3>
        <p className="text-xs text-muted mt-0.5">
          When disabled, the sign-in page hides Register, and a first-time
          single sign-on user is refused unless an auth group IdP bind admits
          them. When the Users auth group has an IdP bind, only members of a
          bound IdP group are admitted, whatever this setting is.
          Administrators can still create users from Users.{" "}
          <code className="text-ink">REGISTER_ENABLED</code> only sets the value on
          the first boot; after that, this setting is kept across restarts.
        </p>
      </div>
      <div className="settings-group-picker" role="group" aria-label="Registration">
        <button
          type="button"
          className={`settings-group-chip ${enabled === true ? "settings-group-chip-active" : ""}`}
          aria-pressed={enabled === true}
          disabled={busy || enabled === null}
          onClick={() => void setRegistration(true)}
        >
          <FontAwesomeIcon icon={faToggleOn} className="opacity-70" />
          <span>Enabled</span>
        </button>
        <button
          type="button"
          className={`settings-group-chip ${enabled === false ? "settings-group-chip-active" : ""}`}
          aria-pressed={enabled === false}
          disabled={busy || enabled === null}
          onClick={() => void setRegistration(false)}
        >
          <FontAwesomeIcon icon={faToggleOff} className="opacity-70" />
          <span>Disabled</span>
        </button>
      </div>
    </div>
  );
}
