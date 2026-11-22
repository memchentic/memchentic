import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faFloppyDisk, faPen, faPlus, faTrash, faXmark } from "@fortawesome/free-solid-svg-icons";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api } from "../api";
import { spaceLabel, useAuth, writableSpaces } from "../auth";
import { ComposerReveal } from "../components/overlay";
import { ErrorNote, NewToggleButton, PageHeader } from "../components/page";
import { errorMessage } from "../errors";

type Project = {
  id: string;
  space_id: string;
  name: string;
  created_by: string | null;
};

export default function ProjectsPage() {
  const { me, refresh } = useAuth();
  const spaces = writableSpaces(me);
  const [spaceId, setSpaceId] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!spaceId && spaces[0]) setSpaceId(spaces[0].id);
  }, [spaces, spaceId]);

  const load = useCallback(async () => {
    if (!spaceId) {
      setProjects([]);
      setLoaded(true);
      return;
    }
    try {
      const rows = await api<Project[]>(`/api/projects?space_id=${encodeURIComponent(spaceId)}`);
      setProjects(rows);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoaded(true);
    }
  }, [spaceId]);

  useEffect(() => {
    setLoaded(false);
    void load();
  }, [load]);

  async function create(e: FormEvent) {
    e.preventDefault();
    if (!spaceId || !name.trim() || busy) return;
    setBusy(true);
    try {
      await api("/api/projects", {
        method: "POST",
        body: JSON.stringify({ space_id: spaceId, name: name.trim() }),
      });
      setName("");
      setShowNew(false);
      await Promise.all([load(), refresh()]);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function rename(project: Project) {
    const next = prompt("Rename project", project.name);
    if (!next || next === project.name) return;
    try {
      await api(`/api/projects/${project.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: next }),
      });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  async function remove(project: Project) {
    if (!confirm(`Delete project ${project.name}? Memories stay; they lose this project label.`)) {
      return;
    }
    try {
      await api(`/api/projects/${project.id}`, { method: "DELETE" });
      await load();
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <div>
      <PageHeader title="Projects">
        <NewToggleButton open={showNew} onToggle={() => setShowNew((s) => !s)} />
      </PageHeader>
      <div className="page-body">
        <ErrorNote error={error} />
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <label className="text-xs uppercase tracking-wider text-faint" htmlFor="project-space">
            Space
          </label>
          <select
            id="project-space"
            className="input w-auto"
            value={spaceId}
            onChange={(e) => setSpaceId(e.target.value)}
          >
            {spaces.map((s) => (
              <option key={s.id} value={s.id}>
                {spaceLabel(me, s.id) || s.name}
              </option>
            ))}
          </select>
        </div>
        <ComposerReveal open={showNew}>
          <form className="panel mb-4 grid gap-3 p-4" onSubmit={(e) => void create(e)}>
            <input
              className="input"
              aria-label="Project name"
              placeholder="Project name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <div className="flex gap-2">
              <button className="btn" type="submit" disabled={busy || !name.trim()} title="Save" aria-label="Save">
                <FontAwesomeIcon icon={faFloppyDisk} />
              </button>
              <button
                className="btn"
                type="button"
                title="Cancel"
                aria-label="Cancel"
                onClick={() => {
                  setShowNew(false);
                  setName("");
                }}
              >
                <FontAwesomeIcon icon={faXmark} />
              </button>
            </div>
          </form>
        </ComposerReveal>
        {!loaded ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : projects.length === 0 ? (
          <p className="text-sm text-muted">No projects in this space yet.</p>
        ) : (
          <div className="panel overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th className="table-actions-col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {projects.map((p) => (
                  <tr key={p.id}>
                    <td className="font-medium text-ink">{p.name}</td>
                    <td className="table-actions-col">
                      <button
                        className="btn"
                        type="button"
                        title="Rename"
                        aria-label={`Rename ${p.name}`}
                        onClick={() => void rename(p)}
                      >
                        <FontAwesomeIcon icon={faPen} />
                      </button>
                      <button
                        className="btn ml-2"
                        type="button"
                        title="Delete"
                        aria-label={`Delete ${p.name}`}
                        onClick={() => void remove(p)}
                      >
                        <FontAwesomeIcon icon={faTrash} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {showNew ? null : (
          <p className="mt-4 text-xs text-faint">
            <FontAwesomeIcon icon={faPlus} className="mr-1 opacity-70" />
            Projects label work inside a space. They do not change who can read memories.
          </p>
        )}
      </div>
    </div>
  );
}
