import { useMemo, useState } from "react";
import {
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
} from "recharts";
import { ErrorNote, PageHeader } from "../components/page";
import { SpaceScopeSelect, TypeFilterMenu } from "../memory/MemoryFilters";
import {
  MEMORY_TYPE_TOKENS,
  type MemoryTypeToken,
  type SpaceScope,
  isUnrestrictedTypes,
} from "../memory/types";
import { useApiResource } from "../hooks/useAsyncResource";
import { useReloadOnMemoryEvents } from "../hooks/useReloadOnMemoryEvents";
import { ChartCard } from "./metrics/ChartCard";
import { HorizontalBarChart } from "./metrics/HorizontalBarChart";
import { parseMetricsQuery } from "./metrics/filterQuery";
import {
  SERIES,
  categoryFill,
  kindFill,
  tooltipStyle,
  typeFill,
} from "./metrics/palette";
import type { MemoryMetrics } from "./metrics/types";
import { ZoomableTimeChart } from "./metrics/ZoomableTimeChart";

const RANGES: { value: "7d" | "30d" | "90d" | "all"; label: string }[] = [
  { value: "7d", label: "7d" },
  { value: "30d", label: "30d" },
  { value: "90d", label: "90d" },
  { value: "all", label: "All" },
];

function shortDay(day: string): string {
  const parts = day.split("-");
  if (parts.length !== 3) return day;
  return `${parts[1]}/${parts[2]}`;
}

/** Inclusive calendar day → exclusive next-day ISO for the API ``to`` param. */
function dayToExclusiveEnd(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  const next = new Date(Date.UTC(y, m - 1, d + 1));
  return next.toISOString();
}

function dayToStart(day: string): string {
  return `${day}T00:00:00.000Z`;
}

function summarize<T extends { count: number }>(rows: T[], name: (row: T) => string): string {
  return rows.map((row) => `${name(row)} ${row.count}`).join(", ");
}

type FilterState = {
  range: "7d" | "30d" | "90d" | "all";
  custom: boolean;
  fromDay: string;
  toDay: string;
  spaceScope: SpaceScope;
  kind: "" | "entry" | "guide";
  types: MemoryTypeToken[];
  tags: string[];
  q: string;
};

const DEFAULT_FILTERS: FilterState = {
  range: "30d",
  custom: false,
  fromDay: "",
  toDay: "",
  spaceScope: "",
  kind: "",
  types: [...MEMORY_TYPE_TOKENS],
  tags: [],
  q: "",
};

function buildMetricsQs(f: FilterState): string {
  const qs = new URLSearchParams();
  if (f.custom && f.fromDay && f.toDay) {
    qs.set("from", dayToStart(f.fromDay));
    qs.set("to", dayToExclusiveEnd(f.toDay));
  } else {
    qs.set("range", f.range);
  }
  if (f.spaceScope) qs.set("space_scope", f.spaceScope);
  if (f.kind) qs.set("kind", f.kind);
  if (!isUnrestrictedTypes(f.types)) qs.set("types", f.types.join(","));
  if (f.tags.length) qs.set("tags", f.tags.join(","));
  if (f.q.trim()) qs.set("q", f.q.trim());
  return qs.toString();
}

export default function MetricsPage() {
  const [filters, setFilters] = useState<FilterState>(DEFAULT_FILTERS);
  const [draft, setDraft] = useState("");
  const [presetBeforeZoom, setPresetBeforeZoom] = useState<"7d" | "30d" | "90d" | "all" | null>(
    null,
  );

  const qs = useMemo(() => buildMetricsQs(filters), [filters]);
  const { data, error, loading, reload } = useApiResource<MemoryMetrics>(
    `/api/metrics/memories?${qs}`,
    { clearOnError: true },
  );
  useReloadOnMemoryEvents(reload);

  const totals = data?.totals;
  const created = (data?.created_per_day ?? []).map((row) => ({
    ...row,
    label: shortDay(row.day),
  }));
  const accessed = (data?.accessed_per_day ?? []).map((row) => ({
    ...row,
    label: shortDay(row.day),
  }));
  const byType = [...(data?.by_type ?? [])].reverse();
  const bySpace = [...(data?.by_space ?? [])].reverse();
  const byProject = [...(data?.by_project ?? [])].reverse();
  const topTags = [...(data?.top_tags ?? [])].reverse();
  const topCreators = [...(data?.top_creators ?? [])]
    .map((row) => ({
      ...row,
      label: row.display_name || row.email || row.user_id.slice(0, 8),
    }))
    .reverse();
  const topAccessed = [...(data?.top_accessed ?? [])]
    .map((row) => ({
      ...row,
      label: row.title.length > 28 ? `${row.title.slice(0, 28)}…` : row.title,
      count: row.access_count,
    }))
    .reverse();
  const byKind = data?.by_kind ?? [];
  const bySource = data?.accesses_by_source ?? [];

  function applyQueryDraft(raw: string) {
    const parsed = parseMetricsQuery(raw);
    const hasSpaceToken = /\bspace:/.test(raw);
    setFilters((prev) => ({
      ...prev,
      q: parsed.q,
      kind: parsed.kind || (!/\bkind:/.test(raw) ? prev.kind : ""),
      types: parsed.types.length ? parsed.types : prev.types,
      tags: parsed.tags,
      spaceScope: hasSpaceToken ? parsed.spaceScope : prev.spaceScope,
    }));
    setDraft(raw.trim());
  }

  function onZoom(fromDay: string, toDay: string) {
    setFilters((prev) => {
      if (!prev.custom) setPresetBeforeZoom(prev.range);
      return { ...prev, custom: true, fromDay, toDay };
    });
  }

  function resetZoom() {
    setFilters((prev) => ({
      ...prev,
      custom: false,
      fromDay: "",
      toDay: "",
      range: presetBeforeZoom ?? prev.range,
    }));
    setPresetBeforeZoom(null);
  }

  function clearAll() {
    setFilters(DEFAULT_FILTERS);
    setDraft("");
    setPresetBeforeZoom(null);
  }

  const chips: { key: string; label: string; clear: () => void }[] = [];
  if (filters.custom && filters.fromDay && filters.toDay) {
    chips.push({
      key: "window",
      label: `${filters.fromDay} → ${filters.toDay}`,
      clear: resetZoom,
    });
  }
  if (filters.spaceScope) {
    chips.push({
      key: "space",
      label: `space:${filters.spaceScope}`,
      clear: () => setFilters((p) => ({ ...p, spaceScope: "" })),
    });
  }
  if (filters.kind) {
    chips.push({
      key: "kind",
      label: `kind:${filters.kind}`,
      clear: () => setFilters((p) => ({ ...p, kind: "" })),
    });
  }
  if (!isUnrestrictedTypes(filters.types)) {
    chips.push({
      key: "types",
      label: `types:${filters.types.join(",")}`,
      clear: () => setFilters((p) => ({ ...p, types: [...MEMORY_TYPE_TOKENS] })),
    });
  }
  for (const tag of filters.tags) {
    chips.push({
      key: `tag-${tag}`,
      label: `tag:${tag}`,
      clear: () => setFilters((p) => ({ ...p, tags: p.tags.filter((t) => t !== tag) })),
    });
  }
  if (filters.q.trim()) {
    chips.push({
      key: "q",
      label: `q:${filters.q.trim()}`,
      clear: () => setFilters((p) => ({ ...p, q: "" })),
    });
  }

  return (
    <div>
      <PageHeader title="Metrics" />
      <div className="page-body">
        <p className="mb-4 text-sm text-muted">
          Storage and retrieval for memories you can access. The time window
          drives created/accessed charts and windowed KPIs; drag on a time chart
          to zoom. Facets apply to every panel. Access history starts when this
          instance began recording recalls.
        </p>

        <form
          className="toolbar metrics-filter-bar mb-3"
          onSubmit={(e) => {
            e.preventDefault();
            applyQueryDraft(draft);
          }}
        >
          <div className="toolbar-search metrics-query-wrap">
            <input
              className="input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Filter: kind:entry type:fact tag:ops space:personal …"
              aria-label="Metrics query"
            />
            <button type="submit" className="btn btn-secondary">
              Apply
            </button>
          </div>
        </form>

        <div className="toolbar metrics-filter-bar mb-3">
          <div className="metrics-range-presets" role="group" aria-label="Time range">
            {RANGES.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={`metrics-range-chip${
                  !filters.custom && filters.range === opt.value
                    ? " metrics-range-chip-active"
                    : ""
                }`}
                onClick={() =>
                  setFilters((p) => ({
                    ...p,
                    custom: false,
                    fromDay: "",
                    toDay: "",
                    range: opt.value,
                  }))
                }
              >
                {opt.label}
              </button>
            ))}
            <button
              type="button"
              className={`metrics-range-chip${filters.custom ? " metrics-range-chip-active" : ""}`}
              onClick={() =>
                setFilters((p) => ({
                  ...p,
                  custom: true,
                  fromDay: p.fromDay || new Date().toISOString().slice(0, 10),
                  toDay: p.toDay || new Date().toISOString().slice(0, 10),
                }))
              }
            >
              Custom
            </button>
            {filters.custom ? (
              <>
                <label className="field toolbar-field">
                  <span>From</span>
                  <input
                    type="date"
                    className="input w-auto"
                    value={filters.fromDay}
                    onChange={(e) =>
                      setFilters((p) => ({ ...p, fromDay: e.target.value, custom: true }))
                    }
                  />
                </label>
                <label className="field toolbar-field">
                  <span>To</span>
                  <input
                    type="date"
                    className="input w-auto"
                    value={filters.toDay}
                    onChange={(e) =>
                      setFilters((p) => ({ ...p, toDay: e.target.value, custom: true }))
                    }
                  />
                </label>
              </>
            ) : null}
            {filters.custom ? (
              <button type="button" className="btn btn-ghost text-sm" onClick={resetZoom}>
                Reset zoom
              </button>
            ) : null}
          </div>
          <div className="toolbar-divider" aria-hidden="true" />
          <SpaceScopeSelect
            value={filters.spaceScope}
            onChange={(spaceScope) => setFilters((p) => ({ ...p, spaceScope }))}
          />
          <label className="field toolbar-field">
            <span>Kind</span>
            <select
              className="input w-auto"
              value={filters.kind}
              onChange={(e) =>
                setFilters((p) => ({
                  ...p,
                  kind: e.target.value as "" | "entry" | "guide",
                }))
              }
            >
              <option value="">All</option>
              <option value="entry">Entry</option>
              <option value="guide">Guide</option>
            </select>
          </label>
          <TypeFilterMenu
            selected={filters.types}
            onChange={(types) => setFilters((p) => ({ ...p, types }))}
          />
          <button type="button" className="btn btn-ghost text-sm" onClick={clearAll}>
            Clear
          </button>
        </div>

        {chips.length > 0 ? (
          <ul className="metrics-filter-chips mb-6">
            {chips.map((chip) => (
              <li key={chip.key}>
                <button type="button" className="metrics-filter-chip" onClick={chip.clear}>
                  {chip.label}
                  <span aria-hidden="true"> ×</span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <div className="mb-6" />
        )}

        <ErrorNote error={error} />

        {loading && !data ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : data && totals ? (
          <>
            <h2 className="metrics-section-title">Storage</h2>
            <div className="metrics-kpi-grid">
              <div className="metrics-kpi panel">
                <div className="metrics-kpi-label">Total memories</div>
                <div className="metrics-kpi-value">{totals.memories}</div>
              </div>
              <div className="metrics-kpi panel">
                <div className="metrics-kpi-label">Entries / guides</div>
                <div className="metrics-kpi-value">
                  <span style={{ color: SERIES.entries }}>{totals.entries}</span>
                  <span className="metrics-kpi-split">/</span>
                  <span style={{ color: SERIES.guides }}>{totals.guides}</span>
                </div>
              </div>
              <div className="metrics-kpi panel">
                <div className="metrics-kpi-label">Created in window</div>
                <div className="metrics-kpi-value">{totals.created_in_window}</div>
              </div>
              <div
                className={`metrics-kpi panel${totals.expiring_soon > 0 ? " metrics-kpi-warn" : ""}`}
              >
                <div className="metrics-kpi-label">Expiring in 14 days</div>
                <div
                  className="metrics-kpi-value"
                  style={
                    totals.expiring_soon > 0 ? { color: SERIES.warn } : undefined
                  }
                >
                  {totals.expiring_soon}
                </div>
              </div>
              <div className="metrics-kpi panel">
                <div className="metrics-kpi-label">Spaces with memories</div>
                <div className="metrics-kpi-value" style={{ color: SERIES.spaces }}>
                  {totals.spaces}
                </div>
              </div>
            </div>

            <div className="metrics-chart-grid mb-8">
              <ChartCard
                title="Created over time"
                summary={`${created.reduce((n, r) => n + r.entries, 0)} entries and ${created.reduce((n, r) => n + r.guides, 0)} guides created in the selected range. Drag to zoom.`}
                empty={created.every((r) => r.entries === 0 && r.guides === 0)}
              >
                <ZoomableTimeChart
                  data={created}
                  onZoom={onZoom}
                  series={[
                    {
                      dataKey: "entries",
                      name: "entries",
                      stroke: SERIES.entries,
                      fill: SERIES.entries,
                      fillOpacity: 0.45,
                      stackId: "1",
                    },
                    {
                      dataKey: "guides",
                      name: "guides",
                      stroke: SERIES.guides,
                      fill: SERIES.guides,
                      fillOpacity: 0.55,
                      stackId: "1",
                    },
                  ]}
                />
              </ChartCard>

              <ChartCard
                title="Entries vs guides"
                summary={summarize(byKind, (r) => r.kind)}
                empty={byKind.length === 0}
              >
                <div className="metrics-chart-body">
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={byKind}
                        dataKey="count"
                        nameKey="kind"
                        cx="50%"
                        cy="50%"
                        innerRadius={48}
                        outerRadius={72}
                        paddingAngle={2}
                        isAnimationActive={false}
                      >
                        {byKind.map((row) => (
                          <Cell key={row.kind} fill={kindFill(row.kind)} />
                        ))}
                      </Pie>
                      <Tooltip contentStyle={tooltipStyle()} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <ul className="metrics-legend">
                  {byKind.map((row) => (
                    <li key={row.kind}>
                      <span
                        className="metrics-legend-swatch"
                        style={{ background: kindFill(row.kind) }}
                      />
                      {row.kind} ({row.count})
                    </li>
                  ))}
                </ul>
              </ChartCard>

              <ChartCard
                title="By entry type"
                summary={summarize(byType, (r) => r.type)}
                empty={byType.length === 0}
              >
                <HorizontalBarChart
                  data={byType}
                  categoryKey="type"
                  categoryWidth={88}
                  rowKey={(row) => row.type}
                  fill={(row) => typeFill(row.type)}
                />
              </ChartCard>

              <ChartCard
                title="By space"
                summary={summarize(bySpace, (r) => r.name)}
                empty={bySpace.length === 0}
              >
                <HorizontalBarChart
                  data={bySpace}
                  categoryKey="name"
                  rowKey={(row) => row.space_id ?? row.name}
                  fill={(_row, index) => categoryFill(index)}
                />
              </ChartCard>

              <ChartCard
                title="Top tags"
                summary={summarize(topTags, (r) => r.tag)}
                empty={topTags.length === 0}
              >
                <HorizontalBarChart
                  data={topTags}
                  categoryKey="tag"
                  rowKey={(row) => row.tag}
                  fill={(_row, index) => categoryFill(index)}
                />
              </ChartCard>

              <ChartCard
                title="Top creators"
                summary={summarize(topCreators, (r) => r.label)}
                empty={topCreators.length === 0}
              >
                <HorizontalBarChart
                  data={topCreators}
                  categoryKey="label"
                  rowKey={(row) => row.user_id}
                  fill={(_row, index) => categoryFill(index)}
                />
              </ChartCard>

              {byProject.length > 0 ? (
                <ChartCard
                  title="By project"
                  summary={summarize(byProject, (r) => r.name)}
                  empty={false}
                >
                  <HorizontalBarChart
                    data={byProject}
                    categoryKey="name"
                    rowKey={(row) => row.project_id}
                    fill={(_row, index) => categoryFill(index)}
                  />
                </ChartCard>
              ) : null}
            </div>

            <h2 className="metrics-section-title">Retrieval</h2>
            <div className="metrics-kpi-grid">
              <div className="metrics-kpi panel">
                <div className="metrics-kpi-label">Accesses in window</div>
                <div className="metrics-kpi-value" style={{ color: SERIES.spaces }}>
                  {totals.accesses_in_window}
                </div>
              </div>
              <div className="metrics-kpi panel">
                <div className="metrics-kpi-label">Unique memories accessed</div>
                <div className="metrics-kpi-value">{totals.unique_memories_accessed}</div>
              </div>
              <div className="metrics-kpi panel">
                <div className="metrics-kpi-label">Never accessed</div>
                <div className="metrics-kpi-value">{totals.never_accessed}</div>
              </div>
            </div>

            <div className="metrics-chart-grid">
              <ChartCard
                title="Accessed over time"
                summary={`${accessed.reduce((n, r) => n + r.count, 0)} recalls in the selected range. Drag to zoom.`}
                empty={accessed.every((r) => r.count === 0)}
              >
                <ZoomableTimeChart
                  data={accessed}
                  onZoom={onZoom}
                  series={[
                    {
                      dataKey: "count",
                      name: "accesses",
                      stroke: SERIES.spaces,
                      fill: SERIES.spaces,
                      fillOpacity: 0.5,
                    },
                  ]}
                />
              </ChartCard>

              <ChartCard
                title="Top accessed"
                summary={summarize(topAccessed, (r) => r.label)}
                empty={topAccessed.length === 0}
              >
                <HorizontalBarChart
                  data={topAccessed}
                  categoryKey="label"
                  categoryWidth={120}
                  rowKey={(row) => row.memory_id}
                  fill={(_row, index) => categoryFill(index)}
                />
              </ChartCard>

              <ChartCard
                title="Accesses by source"
                summary={summarize(bySource, (r) => r.source)}
                empty={bySource.length === 0}
              >
                <HorizontalBarChart
                  data={bySource}
                  categoryKey="source"
                  rowKey={(row) => row.source}
                  fill={(_row, index) => categoryFill(index)}
                />
              </ChartCard>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
