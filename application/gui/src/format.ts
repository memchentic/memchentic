const MISSING = "—";

function formatWith(
  value: string | null | undefined,
  render: (date: Date) => string,
): string {
  if (!value) return MISSING;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return MISSING;
  return render(date);
}

/** Compact date and time for table rows, for example "Mar 4, 2026, 5:06 AM". */
export function formatWhen(value?: string | null): string {
  return formatWith(value, (date) =>
    date.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }),
  );
}

/** Date and time in the browser's default format. */
export function formatDateTime(value?: string | null): string {
  return formatWith(value, (date) => date.toLocaleString());
}

/** Date only, in the browser's default format. */
export function formatDate(value?: string | null): string {
  return formatWith(value, (date) => date.toLocaleDateString());
}
