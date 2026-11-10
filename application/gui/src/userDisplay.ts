import type { DirectoryUser } from "./apiTypes";

/** OAuth / IdP names that usually carry a human profile name. */
const PROFILE_PROVIDERS = new Set([
  "google",
  "github",
  "okta",
  "authentik",
  "keycloak",
  "auth0",
  "entra",
  "gitlab",
  "generic",
]);

function emailLocalPart(email: string): string {
  const local = email.split("@")[0]?.trim();
  return local || email;
}

/** Prefer IdP display name; fall back to email local-part (same as API defaults). */
export function resolveDisplayName(user: Pick<DirectoryUser, "email" | "display_name">): string {
  const named = user.display_name?.trim();
  return named || emailLocalPart(user.email);
}

/**
 * Whether the profile leans on an external IdP (name-first) vs email/password.
 *
 * Admins see `providers`; when absent, treat as email-primary (invite directory).
 */
export function prefersProfileName(providers: string[] | undefined): boolean {
  if (!providers?.length) return false;
  return providers.some((p) => PROFILE_PROVIDERS.has(p));
}
