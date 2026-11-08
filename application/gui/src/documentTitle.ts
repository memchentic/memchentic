/** Fallback when site name is missing. */
export const DEFAULT_APP_NAME = "Memchentic";

/**
 * Build the browser tab title from an optional page title and the app name.
 *
 * Matches archentic's `formatDocumentTitle`: `App | Page` when the page differs,
 * otherwise just the app name.
 */
export function formatDocumentTitle(
  pageTitle: string | null | undefined,
  appName: string | null | undefined = DEFAULT_APP_NAME,
): string {
  const name = String(appName ?? "").trim() || DEFAULT_APP_NAME;
  const page = String(pageTitle ?? "").trim();
  if (!page || page === name) {
    return name;
  }
  return `${name} | ${page}`;
}
