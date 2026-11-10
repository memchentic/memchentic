import { describe, expect, it } from "vitest";
import { OAUTH_ERROR_FALLBACK, oauthErrorMessage } from "./oauthErrors";

describe("oauthErrorMessage", () => {
  it("never echoes unknown input", () => {
    expect(oauthErrorMessage("Account locked. Call 555-0100")).toBe(OAUTH_ERROR_FALLBACK);
    // A name that exists on Object.prototype must not match either.
    expect(oauthErrorMessage("constructor")).toBe(OAUTH_ERROR_FALLBACK);
  });

  it("maps known codes to fixed messages", () => {
    expect(oauthErrorMessage("access_denied")).toBe(
      "Sign-in was cancelled at the identity provider.",
    );
    expect(oauthErrorMessage("Invalid OAuth state")).toBe("Sign-in expired. Try again.");
    expect(
      oauthErrorMessage("Sign-up is disabled for this instance; ask an administrator for access"),
    ).toBe("Sign-up is disabled on this server. Ask an administrator for access.");
  });

  it("returns null without a value", () => {
    expect(oauthErrorMessage(null)).toBeNull();
    expect(oauthErrorMessage("")).toBeNull();
  });
});
