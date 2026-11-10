import { useEffect, useRef, useState, type ReactNode } from "react";
import { usePresence } from "../hooks/usePresence";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Shared overlay dialog with enter/exit motion.
 *
 * Focus moves into the panel when it opens, Tab stays inside it, Escape
 * closes it, and focus returns to the opener when it closes.
 */
export function Modal({
  open,
  onClose,
  labelledBy,
  children,
  closeDisabled = false,
}: {
  open: boolean;
  onClose: () => void;
  labelledBy?: string;
  children: ReactNode;
  closeDisabled?: boolean;
}) {
  const { mounted, leaving } = usePresence(open);
  const panelRef = useRef<HTMLDivElement>(null);

  // Remember what had focus when the dialog opened; give it back on close.
  useEffect(() => {
    if (!open) return undefined;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    return () => {
      if (opener && opener.isConnected) opener.focus();
    };
  }, [open]);

  // The panel mounts one render after `open` turns true. Focus it unless a
  // child with autoFocus already took focus.
  useEffect(() => {
    if (!open || !mounted) return;
    const panel = panelRef.current;
    if (panel && !panel.contains(document.activeElement)) panel.focus();
  }, [open, mounted]);

  useEffect(() => {
    if (!open) return undefined;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        if (!closeDisabled) onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const panel = panelRef.current;
      if (!panel) return;
      const items = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
      const first = items[0];
      const last = items[items.length - 1];
      if (!first || !last) {
        event.preventDefault();
        panel.focus();
        return;
      }
      const current = document.activeElement;
      if (event.shiftKey) {
        if (current === first || current === panel || !panel.contains(current)) {
          event.preventDefault();
          last.focus();
        }
      } else if (current === last || !panel.contains(current)) {
        event.preventDefault();
        first.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose, closeDisabled]);

  if (!mounted) return null;

  return (
    <div
      className={`overlay-backdrop${leaving ? " overlay-backdrop-leave" : ""}`}
      onClick={(e) => {
        if (e.target === e.currentTarget && !closeDisabled) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="overlay-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
      >
        {children}
      </div>
    </div>
  );
}

/** Height-reveal shell for New composers so the table does not jump. */
export function ComposerReveal({
  open,
  children,
}: {
  open: boolean;
  children: ReactNode;
}) {
  const { mounted, leaving } = usePresence(open);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (open && mounted && !leaving) {
      const id = requestAnimationFrame(() => {
        requestAnimationFrame(() => setExpanded(true));
      });
      return () => cancelAnimationFrame(id);
    }
    setExpanded(false);
    return undefined;
  }, [open, mounted, leaving]);

  if (!mounted) return null;

  return (
    <div className={`composer-reveal${expanded ? " composer-reveal-open" : ""}`}>
      <div className="composer-reveal-inner">{children}</div>
    </div>
  );
}
