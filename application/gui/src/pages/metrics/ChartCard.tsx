import type { ReactNode } from "react";

export function ChartCard({
  title,
  summary,
  empty,
  emptyLabel = "No data yet",
  className,
  children,
}: {
  title: string;
  /** Text alternative read instead of the chart. */
  summary: string;
  empty?: boolean;
  /** Shown when ``empty`` is true. */
  emptyLabel?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <section className={`metrics-chart panel${className ? ` ${className}` : ""}`}>
      <h3 className="metrics-chart-title">{title}</h3>
      {empty ? (
        <div className="metrics-chart-empty" role="status" aria-label={`${title}: ${emptyLabel}`}>
          <p className="metrics-chart-empty-label">{emptyLabel}</p>
        </div>
      ) : (
        <div role="img" aria-label={`${title}: ${summary}`}>
          {children}
        </div>
      )}
    </section>
  );
}
