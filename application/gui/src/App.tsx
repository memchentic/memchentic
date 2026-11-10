import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import ApiKeysPage from "./pages/ApiKeysPage";
import EntryPage from "./pages/EntryPage";
import MemoriesPage from "./pages/MemoriesPage";
import MetricsPage from "./pages/MetricsPage";
import SettingsPage from "./pages/SettingsPage";
import SignInPage from "./pages/SignInPage";
import GroupDetailPage from "./pages/GroupDetailPage";
import GroupsPage from "./pages/GroupsPage";
import UserDetailPage from "./pages/UserDetailPage";
import UsersPage from "./pages/UsersPage";

function Private({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div className="centered-panel">
        <section>Checking session…</section>
      </div>
    );
  }
  if (!me) {
    // Remember the requested page; SignInPage returns there after sign-in.
    const from = `${location.pathname}${location.search}`;
    return <Navigate to="/sign-in" replace state={{ from }} />;
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/sign-in" element={<SignInPage />} />
      <Route
        path="/"
        element={
          <Private>
            <Layout />
          </Private>
        }
      >
        <Route index element={<MemoriesPage />} />
        <Route path="entries/:id" element={<EntryPage />} />
        <Route path="metrics" element={<MetricsPage />} />
        <Route path="groups" element={<GroupsPage />} />
        <Route path="groups/:id" element={<GroupDetailPage />} />
        <Route path="users" element={<UsersPage />} />
        <Route path="users/:id" element={<UserDetailPage />} />
        <Route path="api-keys" element={<ApiKeysPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
}
