/**
 * Messages for the `error` query parameter of `/sign-in`.
 *
 * The parameter is attacker-controllable (anyone can craft the link), so its
 * value is never rendered. Known values map to fixed text; everything else
 * maps to `OAUTH_ERROR_FALLBACK`.
 */
const OAUTH_ERROR_MESSAGES = new Map<string, string>([
  ["access_denied", "Sign-in was cancelled at the identity provider."],
  ["Invalid OAuth state", "Sign-in expired. Try again."],
  ["Missing OAuth nonce", "Sign-in expired. Try again."],
  ["Missing authorization code", "The identity provider did not complete sign-in. Try again."],
  ["Web OAuth is not configured", "Single sign-on is not configured on this server."],
  ["OAuth email must be verified", "Your identity provider has not verified your email address."],
  ["OAuth email is required", "Your identity provider did not share an email address."],
  [
    "Sign-up is disabled for this instance; ask an administrator for access",
    "Sign-up is disabled on this server. Ask an administrator for access.",
  ],
]);

export const OAUTH_ERROR_FALLBACK = "Single sign-on failed. Try again.";

/** Fixed message for an `error` query value; null when there is no value. */
export function oauthErrorMessage(raw: string | null): string | null {
  if (!raw) return null;
  return OAUTH_ERROR_MESSAGES.get(raw) ?? OAUTH_ERROR_FALLBACK;
}
