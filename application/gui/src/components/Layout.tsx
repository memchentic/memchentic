import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faBook,
  faChevronDown,
  faGear,
  faKey,
  faRightFromBracket,
  faUsers,
  faUserGroup,
} from "@fortawesome/free-solid-svg-icons";
import { useEffect, useId, useRef, useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";
import { usePresence } from "./ui";

const links = [
  { to: "/", label: "Memories", icon: faBook, end: true },
  { to: "/groups", label: "Groups", icon: faUserGroup },
  { to: "/users", label: "Users", icon: faUsers },
];

function UserMenu() {
  const { me, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const { mounted, leaving } = usePresence(open);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const label = me?.display_name || me?.email || "Account";

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="user-menu" ref={rootRef}>
      <button
        aria-controls={menuId}
        aria-expanded={open}
        aria-haspopup="menu"
        className="nav-control user-menu-trigger"
        onClick={() => setOpen((value) => !value)}
        type="button"
      >
        <span className="user-menu-label truncate max-w-48">{label}</span>
        <FontAwesomeIcon
          icon={faChevronDown}
          className={`user-menu-chevron ${open ? "user-menu-chevron-open" : ""}`}
        />
      </button>
      {mounted ? (
        <div
          className={`user-menu-panel${leaving ? " user-menu-panel-leave" : ""}`}
          id={menuId}
          role="menu"
          aria-label="Account"
        >
          {me?.is_admin ? (
            <Link
              className="user-menu-item"
              role="menuitem"
              to="/settings"
              onClick={() => setOpen(false)}
            >
              <FontAwesomeIcon icon={faGear} className="opacity-70" />
              System Settings
            </Link>
          ) : null}
          <Link
            className="user-menu-item"
            role="menuitem"
            to="/api-keys"
            onClick={() => setOpen(false)}
          >
            <FontAwesomeIcon icon={faKey} className="opacity-70" />
            API Keys
          </Link>
          <button
            className="user-menu-item"
            role="menuitem"
            type="button"
            onClick={() => {
              setOpen(false);
              void logout();
            }}
          >
            <FontAwesomeIcon icon={faRightFromBracket} className="opacity-70" />
            Sign Out
          </button>
        </div>
      ) : null}
    </div>
  );
}

export default function Layout() {
  return (
    <div className="app-shell">
      <nav className="shell-nav" aria-label="Main">
        <Link className="shell-brand" to="/">
          <img
            alt=""
            aria-hidden="true"
            className="shell-brand-mark"
            src="/favicon.svg"
          />
          <span className="shell-brand-wordmark">memchentic</span>
        </Link>

        <div className="nav-primary max-md:hidden">
          {links.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
              className={({ isActive }) =>
                `nav-link ${isActive ? "nav-link-active" : ""}`
              }
            >
              <FontAwesomeIcon icon={l.icon} className="opacity-70" />
              {l.label}
            </NavLink>
          ))}
        </div>

        <div className="nav-actions">
          <UserMenu />
        </div>
      </nav>

      {/* Mobile: primary links as a second hairline strip (still one chrome language). */}
      <div className="flex flex-wrap gap-1 border-b border-border px-3 py-1.5 md:hidden">
        {links.map((l) => (
          <NavLink
            key={l.to}
            to={l.to}
            end={l.end}
            className={({ isActive }) =>
              `nav-link ${isActive ? "nav-link-active" : ""}`
            }
          >
            <FontAwesomeIcon icon={l.icon} className="opacity-70" />
            {l.label}
          </NavLink>
        ))}
      </div>

      <main className="shell-main">
        <div className="page">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
