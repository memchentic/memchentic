import { CHROME } from "./palette";

type PayloadItem = {
  name?: string;
  value?: number | string;
  color?: string;
  dataKey?: string | number;
};

/** Compact Recharts tooltip: category + count, minimal padding. */
export function MetricsTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: PayloadItem[];
  label?: string | number;
}) {
  if (!active || !payload?.length) return null;
  const rows = payload.filter((p) => p.value !== undefined && p.value !== null);
  if (!rows.length) return null;

  const primary = rows[0];
  const labelText = label != null ? String(label).trim() : "";
  const heading =
    labelText ||
    (primary.name && primary.name !== "count" ? String(primary.name) : "");
  const singleCount =
    rows.length === 1 && (primary.dataKey === "count" || primary.name === "count" || !primary.name);

  return (
    <div
      style={{
        background: "oklch(0.205 0 0)",
        border: "1px solid oklch(0.269 0 0)",
        color: CHROME.ink,
        padding: "4px 8px",
        fontSize: 12,
        lineHeight: 1.2,
        whiteSpace: "nowrap",
      }}
    >
      {heading ? (
        <div style={{ color: CHROME.muted, marginBottom: 2 }}>{heading}</div>
      ) : null}
      {singleCount ? (
        <div
          style={{
            fontSize: 14,
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {formatNum(primary.value)}
        </div>
      ) : (
        rows.map((row) => (
          <div
            key={String(row.dataKey ?? row.name)}
            style={{
              display: "flex",
              gap: 12,
              justifyContent: "space-between",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            <span style={{ color: row.color ?? CHROME.muted }}>{row.name}</span>
            <span style={{ fontWeight: 600 }}>{formatNum(row.value)}</span>
          </div>
        ))
      )}
    </div>
  );
}

function formatNum(value: number | string | undefined): string {
  if (typeof value === "number") return value.toLocaleString();
  return String(value ?? "");
}
