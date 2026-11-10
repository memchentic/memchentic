import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faTrash } from "@fortawesome/free-solid-svg-icons";
import { useState, type MouseEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { formatWhen } from "../format";
import { errorMessage } from "../errors";
import { ErrorNote } from "../components/page";
import type { MemoryRow } from "./types";
import { formatMemoryOwner, distinctSnippet, canForgetMemory } from "./rowFormat";
import { useSpaceName } from "./useSpaceName";

const TAG_PREVIEW = 6;

export function MemoryList({
  rows,
  empty,
  onDeleted,
}: {
  rows: MemoryRow[];
  empty: string;
  onDeleted?: (id: string) => void;
}) {
  const { me } = useAuth();
  const navigate = useNavigate();
  const spaceName = useSpaceName();
  const showScore = rows.some((m) => m.score !== undefined);
  const [error, setError] = useState<string | null>(null);
  const [forgettingId, setForgettingId] = useState<string | null>(null);

  async function forgetRow(e: MouseEvent, id: string) {
    e.stopPropagation();
    if (!confirm("Forget this memory permanently?")) return;
    setForgettingId(id);
    setError(null);
    try {
      await api(`/api/memories/${id}`, { method: "DELETE" });
      onDeleted?.(id);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setForgettingId(null);
    }
  }

  if (!rows.length) {
    return (
      <div className="panel empty-state">
        <p className="text-sm text-faint">{empty}</p>
      </div>
    );
  }

  return (
    <div className="panel overflow-x-auto">
      <ErrorNote error={error} />
      <table className="data-table memory-table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Type</th>
            <th>Tags</th>
            <th>Space</th>
            <th>Owner</th>
            <th>Updated</th>
            {showScore ? <th>Score</th> : null}
            <th className="memory-actions-col">
              <span className="sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => {
            const kindLabel = m.kind === "guide" ? "guide" : m.type ?? "entry";
            const visibleTags = m.tags.slice(0, TAG_PREVIEW);
            const hiddenCount = m.tags.length - visibleTags.length;
            const snippet = distinctSnippet(m.title, m.snippet);
            return (
              <tr
                key={m.id}
                className="cursor-pointer"
                onClick={() => navigate(`/entries/${m.id}`)}
              >
                <td>
                  <Link
                    to={`/entries/${m.id}`}
                    className="memory-title-link"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {m.title}
                  </Link>
                  {snippet ? <p className="memory-snippet">{snippet}</p> : null}
                </td>
                <td>
                  <span className={`kind-chip kind-chip-${kindLabel}`}>
                    {kindLabel}
                  </span>
                </td>
                <td>
                  {m.tags.length > 0 ? (
                    <div className="memory-tags">
                      {visibleTags.map((t) => (
                        <span key={t} className="tag">
                          {t}
                        </span>
                      ))}
                      {hiddenCount > 0 ? (
                        <span className="memory-tags-more" title={`${hiddenCount} more tags`}>
                          +{hiddenCount}
                        </span>
                      ) : null}
                    </div>
                  ) : (
                    <span className="text-faint">—</span>
                  )}
                </td>
                <td className="text-muted">{spaceName(m.space_id) || "—"}</td>
                <td className="text-muted whitespace-nowrap">{formatMemoryOwner(m, me)}</td>
                <td className="text-faint whitespace-nowrap">
                  {formatWhen(m.updated_at ?? m.created_at)}
                </td>
                {showScore ? (
                  <td className="text-faint tabular-nums">
                    {m.score !== undefined ? m.score.toFixed(3) : "—"}
                  </td>
                ) : null}
                <td className="memory-actions-col">
                  {canForgetMemory(m, me) ? (
                    <button
                      className="btn memory-row-forget"
                      type="button"
                      title="Forget"
                      aria-label={`Forget ${m.title}`}
                      disabled={forgettingId === m.id}
                      onClick={(e) => void forgetRow(e, m.id)}
                    >
                      <FontAwesomeIcon icon={faTrash} />
                    </button>
                  ) : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
