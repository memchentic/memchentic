import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, PageHeader } from "../components/ui";

type User = {
  id: string;
  email: string;
  display_name: string | null;
  providers?: string[];
  created_at?: string | null;
};

export default function UsersPage() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const [users, setUsers] = useState<User[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<User[]>("/api/users").then(setUsers).catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, []);

  return (
    <div>
      <PageHeader title="Users" />
      <div className="page-body">
      {!me?.is_admin && (
        <p className="mb-4 text-xs text-faint">
          Directory of authenticated users (for team invites). Providers and join date are admin-only.
        </p>
      )}
      <ErrorNote error={error} />
      <div className="panel overflow-x-auto">
        <table className="data-table">
          <thead>
            <tr>
              <th>Email</th>
              <th>Name</th>
              {me?.is_admin && (
                <>
                  <th>Providers</th>
                  <th>Joined</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr
                key={u.id}
                className="cursor-pointer"
                onClick={() => navigate(`/users/${u.id}`)}
              >
                <td className="text-ink">{u.email}</td>
                <td className="text-muted">{u.display_name ?? "—"}</td>
                {me?.is_admin && (
                  <>
                    <td className="text-muted">
                      {u.providers?.length ? u.providers.join(", ") : "—"}
                    </td>
                    <td className="text-faint">
                      {u.created_at ? new Date(u.created_at).toLocaleDateString() : "—"}
                    </td>
                  </>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      </div>
    </div>
  );
}
