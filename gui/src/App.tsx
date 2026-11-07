import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import ApiKeysPage from "./pages/ApiKeysPage";
import BrowsePage from "./pages/BrowsePage";
import EntryPage from "./pages/EntryPage";
import SearchPage from "./pages/SearchPage";
import SignInPage from "./pages/SignInPage";
import TeamDetailPage from "./pages/TeamDetailPage";
import TeamsPage from "./pages/TeamsPage";
import UsersPage from "./pages/UsersPage";

function Private({ children }: { children: React.ReactNode }) {
  const { me, loading } = useAuth();
  if (loading) {
    return (
      <div className="centered-panel">
        <section>Checking session…</section>
      </div>
    );
  }
  if (!me) return <Navigate to="/sign-in" replace />;
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
        <Route index element={<BrowsePage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="entries/:id" element={<EntryPage />} />
        <Route path="teams" element={<TeamsPage />} />
        <Route path="teams/:id" element={<TeamDetailPage />} />
        <Route path="users" element={<UsersPage />} />
        <Route path="api-keys" element={<ApiKeysPage />} />
      </Route>
    </Routes>
  );
}
