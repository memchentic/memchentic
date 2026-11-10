import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faChevronDown } from "@fortawesome/free-solid-svg-icons";
import { useEffect, useId, useRef, useState } from "react";
import { useAuth, writableSpaces } from "../auth";
import { usePresence } from "../hooks/usePresence";
import {
  MEMORY_TYPE_TOKENS,
  type MemoryTypeToken,
  type SpaceScope,
  isUnrestrictedTypes,
} from "./types";

export function SpaceScopeSelect({
  value,
  onChange,
}: {
  value: SpaceScope;
  onChange: (v: SpaceScope) => void;
}) {
  return (
    <label className="field toolbar-field">
      <span>Space</span>
      <select
        className="input w-auto"
        value={value}
        onChange={(e) => onChange(e.target.value as SpaceScope)}
      >
        <option value="">All</option>
        <option value="group">Groups</option>
        <option value="personal">Personal</option>
      </select>
    </label>
  );
}

export function TypeFilterMenu({
  selected,
  onChange,
}: {
  selected: MemoryTypeToken[];
  onChange: (next: MemoryTypeToken[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const { mounted, leaving } = usePresence(open);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  const labelId = useId();
  const allSelected = isUnrestrictedTypes(selected);
  const singleType = !allSelected && selected.length === 1 ? selected[0] : null;
  const label = allSelected
    ? "All"
    : singleType
      ? singleType
      : `${selected.length} types`;

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: globalThis.MouseEvent) {
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

  function toggle(token: MemoryTypeToken) {
    const current = selected.length === 0 ? [...MEMORY_TYPE_TOKENS] : selected;
    if (current.includes(token)) {
      const next = current.filter((t) => t !== token);
      // Empty means unrestricted; snap back to all selected.
      onChange(next.length === 0 ? [...MEMORY_TYPE_TOKENS] : next);
    } else {
      onChange([...current, token]);
    }
  }

  return (
    <div className="field toolbar-field">
      <span id={labelId}>Type</span>
      <div className="filter-menu" ref={rootRef}>
        <button
          aria-controls={menuId}
          aria-expanded={open}
          aria-labelledby={labelId}
          className="input w-auto filter-menu-trigger"
          onClick={() => setOpen((v) => !v)}
          type="button"
        >
          <span
            className={`filter-menu-label truncate${singleType ? ` type-tint kind-chip-${singleType}` : ""}`}
          >
            {label}
          </span>
          <FontAwesomeIcon
            icon={faChevronDown}
            className={`filter-menu-chevron ${open ? "filter-menu-chevron-open" : ""}`}
          />
        </button>
        {mounted ? (
          <div
            className={`filter-menu-panel${leaving ? " filter-menu-panel-leave" : ""}`}
            id={menuId}
            role="group"
            aria-labelledby={labelId}
          >
            {MEMORY_TYPE_TOKENS.map((token) => {
              const checked = selected.length === 0 || selected.includes(token);
              return (
                <label
                  key={token}
                  className="filter-menu-item"
                >
                  <input type="checkbox" checked={checked} onChange={() => toggle(token)} />
                  <span className={`type-tint kind-chip-${token}`}>{token}</span>
                </label>
              );
            })}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function SpaceSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const { me } = useAuth();
  const options = writableSpaces(me);
  // `value` is not one of the options in two cases, and each gets one extra
  // option that carries `value` itself, so the label and the request agree.
  // "": `NewMemory.submit` sends `space_id: null` and the server writes to
  // the caller's personal space, so the option is labelled "Personal".
  // Any other value is a space the caller can no longer write to: it is sent
  // unchanged and the server refuses it, so the option says so.
  const known = options.some((s) => s.id === value);
  return (
    <select className="input w-auto" value={value} onChange={(e) => onChange(e.target.value)}>
      {known ? null : (
        <option value={value}>{value ? "Unavailable space" : "Personal"}</option>
      )}
      {options.map((s) => (
        <option key={s.id} value={s.id}>
          {s.kind === "personal" ? "Personal" : s.name}
        </option>
      ))}
    </select>
  );
}
