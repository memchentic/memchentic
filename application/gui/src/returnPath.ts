/**
 * Path to open after sign-in, read from the router state that `<Private>`
 * writes when it redirects a signed-out visitor.
 *
 * Only application paths are accepted: a string that starts with exactly one
 * "/" ("//host" and "/\host" are refused, because browsers treat them as
 * another origin) and that is not the sign-in page itself. Anything else
 * returns "/".
 */
export function safeReturnPath(state: unknown): string {
  if (!state || typeof state !== "object") return "/";
  const from = (state as { from?: unknown }).from;
  if (typeof from !== "string") return "/";
  if (!from.startsWith("/") || from.startsWith("//") || from.startsWith("/\\")) return "/";
  if (from === "/sign-in" || from.startsWith("/sign-in?") || from.startsWith("/sign-in/")) {
    return "/";
  }
  return from;
}
