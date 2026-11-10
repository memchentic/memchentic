import type { ReactNode } from "react";

export function ChartCard({
  title,
  summary,
  empty,
  children,
}: {
  title: string;
  /** Text alternative read instead of the chart. */
  summary: string;
  empty?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="metrics-chart panel">
      <h3 className="metrics-chart-title">{title}</h3>
      {empty ? (
        <p className="text-sm text-muted">No data yet.</p>
      ) : (
        <div role="img" aria-label={`${title}: ${summary}`}>
          {children}
        </div>
      )}
    </section>
  );
}
