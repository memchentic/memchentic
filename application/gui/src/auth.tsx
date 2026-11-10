import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  api,
  getToken,
  isUnauthorized,
  refreshSession,
  setSessionLostHandler,
  setToken,
} from "./api";

export type Me = {
  id: string | null;
  subject: string;
  email?: string | null;
  display_name?: string | null;
  provider?: string | null;
  is_admin: boolean;
  personal_space_id?: string | null;
  default_shared_space_id?: string | null;
  // `created_by` is the user id of the space's creator; absent on older servers.
  memberships: { id: string; name: string; kind: string; created_by?: string | null }[];
  pending_invites?: {
    id: string;
    name: string;
    creator_email: string | null;
    creator_display_name: string | null;
  }[];
};

/** Spaces the caller may pick as a write target: every group, plus only their own personal space. */
export function writableSpaces(me: Me | null): Me["memberships"] {
  if (!me) return [];
  return me.memberships.filter((s) => s.kind !== "personal" || s.id === me.personal_space_id);
}

/** Label for a space id from the caller's point of view; "" when the id is not in `memberships`. */
export function spaceLabel(me: Me | null, id: string | null | undefined): string {
  const space = me?.memberships.find((m) => m.id === id);
  if (!space) return "";
  if (space.kind !== "personal") return space.name;
  return space.id === me?.personal_space_id ? "Personal" : "Personal (another user)";
}

type AuthState = {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string, remember?: boolean) => Promise<void>;
  register: (
    email: string,
    password: string,
    displayName?: string,
    remember?: boolean,
  ) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  /** Load `/api/me` into state; true on success. Only HTTP 401 clears the signed-in state. */
  const loadMe = useCallback(async (): Promise<boolean> => {
    try {
      setMe(await api<Me>("/api/me"));
      return true;
    } catch (err) {
      if (isUnauthorized(err)) {
        setToken(null);
        setMe(null);
      }
      // Any other failure (5xx, network) keeps the current `me`.
      return false;
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      if (!getToken()) {
        const outcome = await refreshSession();
        if (outcome === "denied") {
          setMe(null);
          return;
        }
        // "error": the server could not be reached. Keep the current state;
        // at start-up `me` is still null, so the sign-in page is shown.
        if (outcome !== "ok") return;
      }
      await loadMe();
    } finally {
      setLoading(false);
    }
  }, [loadMe]);

  useEffect(() => {
    // The server ended the session (expired, revoked, or signed out in another
    // tab): drop local auth state so <Private> sends the user to /sign-in.
    setSessionLostHandler(() => {
      setToken(null);
      setMe(null);
      setLoading(false);
    });
    return () => setSessionLostHandler(null);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(
    async (email: string, password: string, remember = false) => {
      const data = await api<{ access_token: string }>("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password, remember }),
      });
      setToken(data.access_token);
      if (!(await loadMe())) {
        throw new Error("Signed in, but your profile could not be loaded. Try again.");
      }
    },
    [loadMe],
  );

  const register = useCallback(
    async (email: string, password: string, displayName?: string, remember = false) => {
      await api("/api/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password, display_name: displayName }),
      });
      await login(email, password, remember);
    },
    [login],
  );

  const logout = useCallback(async () => {
    // Rejects when the server did not confirm the logout. Local state is kept
    // in that case: the session cookie is still valid, so showing the sign-in
    // page would be false.
    await api("/api/auth/logout", { method: "POST" });
    setToken(null);
    setMe(null);
  }, []);

  const value = useMemo(
    () => ({ me, loading, login, register, logout, refresh }),
    [me, loading, login, register, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside provider");
  return ctx;
}
