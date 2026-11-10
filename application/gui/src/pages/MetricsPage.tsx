import { useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ErrorNote, PageHeader } from "../components/page";
import { SpaceScopeSelect } from "../memory/MemoryFilters";
import type { SpaceScope } from "../memory/types";
import { useApiResource } from "../hooks/useAsyncResource";
import { CHROME, SERIES, typeFill, kindFill, categoryFill, tooltipStyle } from "./metrics/palette";
import { ChartCard } from "./metrics/ChartCard";
import { HorizontalBarChart } from "./metrics/HorizontalBarChart";

type MetricRange = "7d" | "30d" | "90d" | "all";

type MemoryMetrics = {
  range: MetricRange;
  generated_at: string;
  totals: {
    memories: number;
    entries: number;
    guides: number;
    expiring_soon: number;
    spaces: number;
  };
  created_per_day: { day: string; entries: number; guides: number }[];
  by_kind: { kind: string; count: number }[];
  by_type: { type: string; count: number }[];
  by_space: { space_id: string | null; name: string; count: number }[];
  top_tags: { tag: string; count: number }[];
  top_creators: {
    user_id: string;
    email: string | null;
    display_name: string | null;
    count: number;
  }[];
};

const RANGES: { value: MetricRange; label: string }[] = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
  { value: "all", label: "All time" },
];

function shortDay(day: string): string {
  const parts = day.split("-");
  if (parts.length !== 3) return day;
  return `${parts[1]}/${parts[2]}`;
}

/** "fact 3, note 2" text for a chart summary. */
function summarize<T extends { count: number }>(rows: T[], name: (row: T) => string): string {
  return rows.map((row) => `${name(row)} ${row.count}`).join(", ");
}

export default function MetricsPage() {
  const [range, setRange] = useState<MetricRange>("30d");
  const [spaceScope, setSpaceScope] = useState<SpaceScope>("");
  const qs = new URLSearchParams({ range });
  if (spaceScope) qs.set("space_scope", spaceScope);
  const { data, error, loading } = useApiResource<MemoryMetrics>(
    `/api/metrics/memories?${qs}`,
    { clearOnError: true },
  );

  const totals = data?.totals;
  const created = (data?.created_per_day ?? []).map((row) => ({
    ...row,
    label: shortDay(row.day),
  }));
  const byType = [...(data?.by_type ?? [])].reverse();
  const bySpace = [...(data?.by_space ?? [])].reverse();
  const topTags = [...(data?.top_tags ?? [])].reverse();
  const topCreators = [...(data?.top_creators ?? [])]
    .map((row) => ({
      ...row,
      label: row.display_name || row.email || row.user_id.slice(0, 8),
    }))
    .reverse();
  const byKind = data?.by_kind ?? [];

  return (
    <div>
      <PageHeader title="Metrics" />
      <div className="page-body">
        <p className="mb-4 text-sm text-muted">
          Corpus snapshot for memories you can access. The created-over-time chart
          follows the selected window; totals and breakdowns cover the full visible
          corpus.
        </p>
        <form className="toolbar mb-6" onSubmit={(e) => e.preventDefault()}>
          <label className="field toolbar-field">
            <span>Range</span>
            <select
              className="input w-auto"
              value={range}
              onChange={(e) => setRange(e.target.value as MetricRange)}
            >
              {RANGES.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          <div className="toolbar-divider" aria-hidden="true" />
          <SpaceScopeSelect value={spaceScope} onChange={setSpaceScope} />
        </form>

        <ErrorNote error={error} />

        {loading && !data ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : data && totals ? (
          <>
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

            <div className="metrics-chart-grid">
              <ChartCard
                title="Created over time"
                summary={`${created.reduce((n, r) => n + r.entries, 0)} entries and ${created.reduce((n, r) => n + r.guides, 0)} guides created in the selected range`}
                empty={created.every((r) => r.entries === 0 && r.guides === 0)}
              >
                <div className="metrics-chart-body metrics-chart-body-wide">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={created} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                      <CartesianGrid stroke={CHROME.grid} strokeDasharray="3 3" />
                      <XAxis
                        dataKey="label"
                        tick={{ fill: CHROME.muted, fontSize: 11 }}
                        interval="preserveStartEnd"
                        minTickGap={24}
                      />
                      <YAxis
                        allowDecimals={false}
                        tick={{ fill: CHROME.muted, fontSize: 11 }}
                        width={36}
                      />
                      <Tooltip contentStyle={tooltipStyle()} />
                      <Area
                        type="monotone"
                        dataKey="entries"
                        name="entries"
                        stackId="1"
                        stroke={SERIES.entries}
                        fill={SERIES.entries}
                        fillOpacity={0.45}
                        isAnimationActive={false}
                      />
                      <Area
                        type="monotone"
                        dataKey="guides"
                        name="guides"
                        stackId="1"
                        stroke={SERIES.guides}
                        fill={SERIES.guides}
                        fillOpacity={0.55}
                        isAnimationActive={false}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>

              <ChartCard title="Entries vs guides" summary={summarize(byKind, (r) => r.kind)} empty={byKind.length === 0}>
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

              <ChartCard title="By entry type" summary={summarize(byType, (r) => r.type)} empty={byType.length === 0}>
                <HorizontalBarChart
                  data={byType}
                  categoryKey="type"
                  categoryWidth={88}
                  rowKey={(row) => row.type}
                  fill={(row) => typeFill(row.type)}
                />
              </ChartCard>

              <ChartCard title="By space" summary={summarize(bySpace, (r) => r.name)} empty={bySpace.length === 0}>
                <HorizontalBarChart
                  data={bySpace}
                  categoryKey="name"
                  rowKey={(row) => row.space_id ?? row.name}
                  fill={(_row, index) => categoryFill(index)}
                />
              </ChartCard>

              <ChartCard title="Top tags" summary={summarize(topTags, (r) => r.tag)} empty={topTags.length === 0}>
                <HorizontalBarChart
                  data={topTags}
                  categoryKey="tag"
                  rowKey={(row) => row.tag}
                  fill={(_row, index) => categoryFill(index)}
                />
              </ChartCard>

              <ChartCard title="Top creators" summary={summarize(topCreators, (r) => r.label)} empty={topCreators.length === 0}>
                <HorizontalBarChart
                  data={topCreators}
                  categoryKey="label"
                  rowKey={(row) => row.user_id}
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
