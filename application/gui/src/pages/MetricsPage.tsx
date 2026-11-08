import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api";
import {
  ErrorNote,
  PageHeader,
  SpaceScopeSelect,
  type SpaceScope,
} from "../components/ui";

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

const GREY = {
  ink: "oklch(0.985 0 0)",
  muted: "oklch(0.708 0 0)",
  mid: "oklch(0.55 0 0)",
  strong: "oklch(0.439 0 0)",
  fill: "oklch(0.269 0 0)",
  grid: "oklch(0.269 0 0)",
};

const KIND_COLORS = [GREY.muted, GREY.strong];

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

function ChartCard({
  title,
  empty,
  children,
}: {
  title: string;
  empty?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="metrics-chart panel">
      <h3 className="metrics-chart-title">{title}</h3>
      {empty ? <p className="text-sm text-muted">No data yet.</p> : children}
    </section>
  );
}

function tooltipStyle(): CSSProperties {
  return {
    background: "oklch(0.205 0 0)",
    border: "1px solid oklch(0.269 0 0)",
    borderRadius: 0,
    color: GREY.ink,
    fontSize: 12,
  };
}

export default function MetricsPage() {
  const [range, setRange] = useState<MetricRange>("30d");
  const [spaceScope, setSpaceScope] = useState<SpaceScope>("");
  const [data, setData] = useState<MemoryMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const qs = new URLSearchParams({ range });
      if (spaceScope) qs.set("space_scope", spaceScope);
      const payload = await api<MemoryMetrics>(`/api/metrics/memories?${qs}`);
      setData(payload);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [range, spaceScope]);

  useEffect(() => {
    void load();
  }, [load]);

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
          <select
            className="input w-auto"
            value={range}
            onChange={(e) => setRange(e.target.value as MetricRange)}
            aria-label="Time range"
          >
            {RANGES.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
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
                  {totals.entries}
                  <span className="metrics-kpi-split">/</span>
                  {totals.guides}
                </div>
              </div>
              <div className="metrics-kpi panel">
                <div className="metrics-kpi-label">Expiring in 14 days</div>
                <div className="metrics-kpi-value">{totals.expiring_soon}</div>
              </div>
              <div className="metrics-kpi panel">
                <div className="metrics-kpi-label">Spaces with memories</div>
                <div className="metrics-kpi-value">{totals.spaces}</div>
              </div>
            </div>

            <div className="metrics-chart-grid">
              <ChartCard
                title="Created over time"
                empty={created.every((r) => r.entries === 0 && r.guides === 0)}
              >
                <div className="metrics-chart-body metrics-chart-body-wide">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={created} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                      <CartesianGrid stroke={GREY.grid} strokeDasharray="3 3" />
                      <XAxis
                        dataKey="label"
                        tick={{ fill: GREY.muted, fontSize: 11 }}
                        interval="preserveStartEnd"
                        minTickGap={24}
                      />
                      <YAxis
                        allowDecimals={false}
                        tick={{ fill: GREY.muted, fontSize: 11 }}
                        width={36}
                      />
                      <Tooltip contentStyle={tooltipStyle()} />
                      <Area
                        type="monotone"
                        dataKey="entries"
                        stackId="1"
                        stroke={GREY.muted}
                        fill={GREY.muted}
                        fillOpacity={0.45}
                        isAnimationActive={false}
                      />
                      <Area
                        type="monotone"
                        dataKey="guides"
                        stackId="1"
                        stroke={GREY.strong}
                        fill={GREY.strong}
                        fillOpacity={0.55}
                        isAnimationActive={false}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>

              <ChartCard title="Entries vs guides" empty={byKind.length === 0}>
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
                        {byKind.map((row, idx) => (
                          <Cell
                            key={row.kind}
                            fill={KIND_COLORS[idx % KIND_COLORS.length]}
                          />
                        ))}
                      </Pie>
                      <Tooltip contentStyle={tooltipStyle()} />
                    </PieChart>
                  </ResponsiveContainer>
                </div>
                <ul className="metrics-legend">
                  {byKind.map((row, idx) => (
                    <li key={row.kind}>
                      <span
                        className="metrics-legend-swatch"
                        style={{ background: KIND_COLORS[idx % KIND_COLORS.length] }}
                      />
                      {row.kind} ({row.count})
                    </li>
                  ))}
                </ul>
              </ChartCard>

              <ChartCard title="By entry type" empty={byType.length === 0}>
                <div className="metrics-chart-body">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={byType}
                      layout="vertical"
                      margin={{ top: 4, right: 12, left: 8, bottom: 4 }}
                    >
                      <CartesianGrid stroke={GREY.grid} strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" allowDecimals={false} tick={{ fill: GREY.muted, fontSize: 11 }} />
                      <YAxis
                        type="category"
                        dataKey="type"
                        width={88}
                        tick={{ fill: GREY.muted, fontSize: 11 }}
                      />
                      <Tooltip contentStyle={tooltipStyle()} />
                      <Bar dataKey="count" fill={GREY.muted} isAnimationActive={false} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>

              <ChartCard title="By space" empty={bySpace.length === 0}>
                <div className="metrics-chart-body">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={bySpace}
                      layout="vertical"
                      margin={{ top: 4, right: 12, left: 8, bottom: 4 }}
                    >
                      <CartesianGrid stroke={GREY.grid} strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" allowDecimals={false} tick={{ fill: GREY.muted, fontSize: 11 }} />
                      <YAxis
                        type="category"
                        dataKey="name"
                        width={96}
                        tick={{ fill: GREY.muted, fontSize: 11 }}
                      />
                      <Tooltip contentStyle={tooltipStyle()} />
                      <Bar dataKey="count" fill={GREY.strong} isAnimationActive={false} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>

              <ChartCard title="Top tags" empty={topTags.length === 0}>
                <div className="metrics-chart-body">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={topTags}
                      layout="vertical"
                      margin={{ top: 4, right: 12, left: 8, bottom: 4 }}
                    >
                      <CartesianGrid stroke={GREY.grid} strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" allowDecimals={false} tick={{ fill: GREY.muted, fontSize: 11 }} />
                      <YAxis
                        type="category"
                        dataKey="tag"
                        width={96}
                        tick={{ fill: GREY.muted, fontSize: 11 }}
                      />
                      <Tooltip contentStyle={tooltipStyle()} />
                      <Bar dataKey="count" fill={GREY.mid} isAnimationActive={false} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>

              <ChartCard title="Top creators" empty={topCreators.length === 0}>
                <div className="metrics-chart-body">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={topCreators}
                      layout="vertical"
                      margin={{ top: 4, right: 12, left: 8, bottom: 4 }}
                    >
                      <CartesianGrid stroke={GREY.grid} strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" allowDecimals={false} tick={{ fill: GREY.muted, fontSize: 11 }} />
                      <YAxis
                        type="category"
                        dataKey="label"
                        width={96}
                        tick={{ fill: GREY.muted, fontSize: 11 }}
                      />
                      <Tooltip contentStyle={tooltipStyle()} />
                      <Bar dataKey="count" fill={GREY.fill} isAnimationActive={false} />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </ChartCard>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
