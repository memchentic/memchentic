import { useCallback, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { MetricsTooltip } from "./MetricsTooltip";
import { CHROME, tooltipCursor } from "./palette";

type SeriesSpec = {
  dataKey: string;
  name: string;
  stroke: string;
  fill: string;
  fillOpacity?: number;
  stackId?: string;
};

type Point = { day: string; label: string; [key: string]: string | number };

/**
 * Area chart with click-drag zoom. On release, calls ``onZoom`` with the
 * selected ISO day bounds (inclusive calendar days as from midnight UTC /
 * to next-day exclusive via the parent).
 */
export function ZoomableTimeChart({
  data,
  series,
  onZoom,
  ariaHint,
}: {
  data: Point[];
  series: SeriesSpec[];
  onZoom: (fromDay: string, toDay: string) => void;
  ariaHint?: string;
}) {
  const [refLeft, setRefLeft] = useState<string | null>(null);
  const [refRight, setRefRight] = useState<string | null>(null);
  const dragging = refLeft != null;

  const onMouseDown = useCallback((e: { activeLabel?: string | number }) => {
    if (e?.activeLabel == null) return;
    const label = String(e.activeLabel);
    const point = data.find((row) => row.label === label);
    if (!point) return;
    setRefLeft(point.day);
    setRefRight(point.day);
  }, [data]);

  const onMouseMove = useCallback(
    (e: { activeLabel?: string | number }) => {
      if (!dragging || e?.activeLabel == null) return;
      const label = String(e.activeLabel);
      const point = data.find((row) => row.label === label);
      if (!point) return;
      setRefRight(point.day);
    },
    [data, dragging],
  );

  const onMouseUp = useCallback(() => {
    if (refLeft == null || refRight == null) {
      setRefLeft(null);
      setRefRight(null);
      return;
    }
    const a = refLeft <= refRight ? refLeft : refRight;
    const b = refLeft <= refRight ? refRight : refLeft;
    setRefLeft(null);
    setRefRight(null);
    if (a !== b || data.length > 1) {
      onZoom(a, b);
    }
  }, [data.length, onZoom, refLeft, refRight]);

  const leftLabel = refLeft
    ? data.find((row) => row.day === refLeft)?.label
    : undefined;
  const rightLabel = refRight
    ? data.find((row) => row.day === refRight)?.label
    : undefined;

  return (
    <div
      className="metrics-chart-body metrics-chart-body-wide"
      title={ariaHint ?? "Click and drag to zoom the time range"}
    >
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart
          data={data}
          margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
          onMouseDown={onMouseDown}
          onMouseMove={onMouseMove}
          onMouseUp={onMouseUp}
          onMouseLeave={onMouseUp}
        >
          <CartesianGrid stroke={CHROME.grid} strokeDasharray="3 3" />
          <XAxis
            dataKey="label"
            tick={{ fill: CHROME.muted, fontSize: 11 }}
            interval="preserveStartEnd"
            minTickGap={24}
            allowDataOverflow
          />
          <YAxis
            allowDecimals={false}
            tick={{ fill: CHROME.muted, fontSize: 11 }}
            width={36}
          />
          <Tooltip content={<MetricsTooltip />} cursor={tooltipCursor} />
          {series.map((s) => (
            <Area
              key={s.dataKey}
              type="monotone"
              dataKey={s.dataKey}
              name={s.name}
              stackId={s.stackId}
              stroke={s.stroke}
              fill={s.fill}
              fillOpacity={s.fillOpacity ?? 0.45}
              isAnimationActive={false}
            />
          ))}
          {leftLabel && rightLabel ? (
            <ReferenceArea
              x1={leftLabel}
              x2={rightLabel}
              strokeOpacity={0.3}
              fill={CHROME.ink}
              fillOpacity={0.08}
            />
          ) : null}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
