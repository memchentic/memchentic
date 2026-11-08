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

/** Axis/tooltip chrome stays greyscale; series use kind-chip hues. */
const CHROME = {
  ink: "oklch(0.985 0 0)",
  muted: "oklch(0.708 0 0)",
  grid: "oklch(0.269 0 0)",
};

/** Matches `.kind-chip-*` hues in index.css, bumped chroma for chart fills. */
const TYPE_FILL: Record<string, string> = {
  guide: "oklch(0.68 0.12 230)",
  fact: "oklch(0.68 0.12 250)",
  preference: "oklch(0.68 0.13 310)",
  decision: "oklch(0.72 0.14 85)",
  instruction: "oklch(0.68 0.13 165)",
  feedback: "oklch(0.68 0.14 25)",
  insight: "oklch(0.68 0.14 295)",
  note: "oklch(0.66 0.04 260)",
  entry: "oklch(0.7 0.08 250)",
};

const SERIES = {
  entries: TYPE_FILL.entry,
  guides: TYPE_FILL.guide,
  warn: "oklch(0.72 0.14 55)",
  spaces: "oklch(0.68 0.12 200)",
};

/** Distinct categorical hues for spaces / tags / creators. */
const CATEGORY_FILLS = [
  "oklch(0.68 0.12 230)",
  "oklch(0.72 0.14 85)",
  "oklch(0.68 0.13 165)",
  "oklch(0.68 0.13 310)",
  "oklch(0.68 0.14 25)",
  "oklch(0.68 0.14 295)",
  "oklch(0.68 0.12 250)",
  "oklch(0.7 0.1 145)",
  "oklch(0.7 0.1 40)",
  "oklch(0.66 0.1 200)",
];

function typeFill(type: string): string {
  return TYPE_FILL[type] ?? TYPE_FILL.note;
}

function kindFill(kind: string): string {
  if (kind === "guide" || kind === "guides") return SERIES.guides;
  if (kind === "entry" || kind === "entries") return SERIES.entries;
  return typeFill(kind);
}

function categoryFill(index: number): string {
  return CATEGORY_FILLS[index % CATEGORY_FILLS.length];
}

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
    color: CHROME.ink,
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

              <ChartCard title="By entry type" empty={byType.length === 0}>
                <div className="metrics-chart-body">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart
                      data={byType}
                      layout="vertical"
                      margin={{ top: 4, right: 12, left: 8, bottom: 4 }}
                    >
                      <CartesianGrid stroke={CHROME.grid} strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" allowDecimals={false} tick={{ fill: CHROME.muted, fontSize: 11 }} />
                      <YAxis
                        type="category"
                        dataKey="type"
                        width={88}
                        tick={{ fill: CHROME.muted, fontSize: 11 }}
                      />
                      <Tooltip contentStyle={tooltipStyle()} />
                      <Bar dataKey="count" isAnimationActive={false}>
                        {byType.map((row) => (
                          <Cell key={row.type} fill={typeFill(row.type)} />
                        ))}
                      </Bar>
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
                      <CartesianGrid stroke={CHROME.grid} strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" allowDecimals={false} tick={{ fill: CHROME.muted, fontSize: 11 }} />
                      <YAxis
                        type="category"
                        dataKey="name"
                        width={96}
                        tick={{ fill: CHROME.muted, fontSize: 11 }}
                      />
                      <Tooltip contentStyle={tooltipStyle()} />
                      <Bar dataKey="count" isAnimationActive={false}>
                        {bySpace.map((row, idx) => (
                          <Cell key={row.space_id ?? row.name} fill={categoryFill(idx)} />
                        ))}
                      </Bar>
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
                      <CartesianGrid stroke={CHROME.grid} strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" allowDecimals={false} tick={{ fill: CHROME.muted, fontSize: 11 }} />
                      <YAxis
                        type="category"
                        dataKey="tag"
                        width={96}
                        tick={{ fill: CHROME.muted, fontSize: 11 }}
                      />
                      <Tooltip contentStyle={tooltipStyle()} />
                      <Bar dataKey="count" isAnimationActive={false}>
                        {topTags.map((row, idx) => (
                          <Cell key={row.tag} fill={categoryFill(idx)} />
                        ))}
                      </Bar>
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
                      <CartesianGrid stroke={CHROME.grid} strokeDasharray="3 3" horizontal={false} />
                      <XAxis type="number" allowDecimals={false} tick={{ fill: CHROME.muted, fontSize: 11 }} />
                      <YAxis
                        type="category"
                        dataKey="label"
                        width={96}
                        tick={{ fill: CHROME.muted, fontSize: 11 }}
                      />
                      <Tooltip contentStyle={tooltipStyle()} />
                      <Bar dataKey="count" isAnimationActive={false}>
                        {topCreators.map((row, idx) => (
                          <Cell key={row.user_id} fill={categoryFill(idx)} />
                        ))}
                      </Bar>
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
