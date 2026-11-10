import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CHROME, tooltipStyle } from "./palette";

/** Horizontal bars of `count`, one bar per row, labelled by `categoryKey`. */
export function HorizontalBarChart<T extends { count: number }>({
  data,
  categoryKey,
  categoryWidth = 96,
  rowKey,
  fill,
}: {
  data: T[];
  /** Field shown on the category axis. */
  categoryKey: Extract<keyof T, string>;
  /** Width of the category axis in pixels. */
  categoryWidth?: number;
  /** React key of a row's bar. */
  rowKey: (row: T) => string;
  /** Fill colour of a row's bar. */
  fill: (row: T, index: number) => string;
}) {
  return (
    <div className="metrics-chart-body">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          layout="vertical"
          margin={{ top: 4, right: 12, left: 8, bottom: 4 }}
        >
          <CartesianGrid stroke={CHROME.grid} strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" allowDecimals={false} tick={{ fill: CHROME.muted, fontSize: 11 }} />
          <YAxis
            type="category"
            dataKey={categoryKey}
            width={categoryWidth}
            tick={{ fill: CHROME.muted, fontSize: 11 }}
          />
          <Tooltip contentStyle={tooltipStyle()} />
          <Bar dataKey="count" isAnimationActive={false}>
            {data.map((row, index) => (
              <Cell key={rowKey(row)} fill={fill(row, index)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
