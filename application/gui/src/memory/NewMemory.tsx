import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faFloppyDisk, faXmark } from "@fortawesome/free-solid-svg-icons";
import { useState, type FormEvent } from "react";
import { api } from "../api";
import { ENTRY_TYPES } from "./types";
import { ErrorNote } from "../components/page";
import { SpaceSelect } from "./MemoryFilters";
import { errorMessage } from "../errors";

export function NewMemory({
  defaultSpace,
  onCreated,
  onCancel,
}: {
  defaultSpace: string;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState<"entry" | "guide">("entry");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [type, setType] = useState("note");
  const [tags, setTags] = useState("");
  const [space, setSpace] = useState(defaultSpace);
  const [force, setForce] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    const tagList = tags.split(",").map((t) => t.trim()).filter(Boolean);
    const payload = { title, body, tags: tagList, space_id: space || null };
    setBusy(true);
    try {
      if (kind === "guide") {
        await api("/api/guides", { method: "POST", body: JSON.stringify(payload) });
      } else {
        await api("/api/memories", {
          method: "POST",
          body: JSON.stringify({ ...payload, type, force }),
        });
      }
      onCreated();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="panel composer mb-6" onSubmit={submit}>
      <div className="composer-header">
        <h3 className="composer-title">New memory</h3>
        <button
          className="btn"
          type="button"
          title="Close"
          aria-label="Close"
          onClick={onCancel}
        >
          <FontAwesomeIcon icon={faXmark} />
        </button>
      </div>
      <ErrorNote error={error} />
      <div className="composer-grid">
        <label className="field">
          <span>Kind</span>
          <select
            className="input"
            value={kind}
            onChange={(e) => setKind(e.target.value as "entry" | "guide")}
          >
            <option value="entry">Entry</option>
            <option value="guide">Guide</option>
          </select>
        </label>
        {kind === "entry" && (
          <label className="field">
            <span>Type</span>
            <select className="input" value={type} onChange={(e) => setType(e.target.value)}>
              {ENTRY_TYPES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span>Space</span>
          <SpaceSelect value={space} onChange={setSpace} />
        </label>
        <label className="field field-span">
          <span>Title</span>
          <input
            className="input"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
          />
        </label>
        <label className="field field-span">
          <span>Body</span>
          <textarea
            className="input min-h-28"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            required
          />
        </label>
        <label className="field field-span">
          <span>Tags</span>
          <input
            className="input"
            placeholder="comma, separated"
            value={tags}
            onChange={(e) => setTags(e.target.value)}
          />
        </label>
      </div>
      <div className="composer-footer">
        {kind === "entry" && (
          <label className="flex items-center gap-2 text-sm text-muted">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
            Save even if a near-duplicate exists
          </label>
        )}
        <button
          className="btn ml-auto"
          type="submit"
          title="Save"
          aria-label="Save"
          disabled={busy}
        >
          <FontAwesomeIcon icon={faFloppyDisk} />
        </button>
      </div>
    </form>
  );
}
