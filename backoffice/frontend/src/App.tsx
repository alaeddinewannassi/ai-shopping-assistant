import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { useAuth } from "./lib/auth";
import { Login } from "./pages/Login";
import { Overview } from "./pages/Overview";
import { Funnel } from "./pages/Funnel";
import { Sessions } from "./pages/Sessions";
import { SessionDetail } from "./pages/SessionDetail";
import { Settings } from "./pages/Settings";
import { AdminTenants } from "./pages/AdminTenants";

function ProtectedShell() {
  const { user, loading } = useAuth();
  if (loading) return <p style={{ padding: 24 }}>Loading…</p>;
  if (!user) return <Navigate to="/login" replace />;
  return <AppShell />;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<ProtectedShell />}>
        <Route path="/overview" element={<Overview />} />
        <Route path="/funnel" element={<Funnel />} />
        <Route path="/sessions" element={<Sessions />} />
        <Route path="/sessions/:sessionId" element={<SessionDetail />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/admin/tenants" element={<AdminTenants />} />
        <Route path="/" element={<Navigate to="/overview" replace />} />
      </Route>
    </Routes>
  );
}
