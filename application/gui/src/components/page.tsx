import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faPlus, faXmark } from "@fortawesome/free-solid-svg-icons";
import { useEffect, type ReactNode } from "react";
import { formatDocumentTitle } from "../documentTitle";

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  useEffect(() => {
    document.title = formatDocumentTitle(title);
  }, [title]);

  return (
    <div className="page-header">
      <h2 className="page-header-title">{title}</h2>
      {children ? <div className="page-header-actions">{children}</div> : null}
    </div>
  );
}

/** Header control that opens/closes the shared New composer on list pages. */
export function NewToggleButton({
  open,
  onToggle,
}: {
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      className={`btn${open ? " btn-active" : ""}`}
      type="button"
      onClick={onToggle}
      aria-label={open ? "Cancel" : "New"}
      title={open ? "Cancel" : "New"}
    >
      <FontAwesomeIcon icon={open ? faXmark : faPlus} />
    </button>
  );
}

export function ErrorNote({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <p className="flash-error mb-4" role="alert">
      {error}
    </p>
  );
}
