import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faToggleOn, faUserGroup } from "@fortawesome/free-solid-svg-icons";
import { Navigate, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth";
import { PageHeader } from "../components/page";
import { RegistrationPanel } from "./settings/RegistrationPanel";
import { AuthGroupsPanel } from "./settings/AuthGroupsPanel";

const CATEGORIES = [
  {
    id: "auth-groups",
    label: "Auth Groups",
    icon: faUserGroup,
  },
  {
    id: "registration",
    label: "Registration",
    icon: faToggleOn,
  },
] as const;

export default function SettingsPage() {
  const { me } = useAuth();
  const [params, setParams] = useSearchParams();
  const category = params.get("category") || "auth-groups";

  if (!me?.is_admin) {
    return <Navigate to="/" replace />;
  }

  return (
    <div className="settings-page">
      <PageHeader title="System Settings" />
      <div className="settings-columns">
        <aside className="settings-rail" aria-label="Settings categories">
          <ul className="settings-rail-list">
            {CATEGORIES.map((c) => {
              const active = category === c.id;
              return (
                <li key={c.id} className="settings-rail-item">
                  <button
                    type="button"
                    className={`settings-rail-row ${active ? "settings-rail-row-active" : ""}`}
                    aria-current={active ? "page" : undefined}
                    onClick={() => setParams({ category: c.id })}
                  >
                    <FontAwesomeIcon icon={c.icon} className="settings-rail-icon" />
                    <span className="truncate">{c.label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>
        <section className="settings-main min-w-0 flex-1">
          {category === "auth-groups" ? (
            <div key="auth-groups" className="settings-main-pane">
              <AuthGroupsPanel />
            </div>
          ) : null}
          {category === "registration" ? (
            <div key="registration" className="settings-main-pane">
              <RegistrationPanel />
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
