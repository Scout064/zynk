import { useEffect, useState } from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { api, getToken } from "./api/client";
import { Layout } from "./components/Layout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Devices from "./pages/Devices";
import DeviceDetail from "./pages/DeviceDetail";
import Schedules from "./pages/Schedules";
import Audit from "./pages/Audit";
import About from "./pages/About";
import { Spinner } from "./components/ui";

function Shell() {
  const [username, setUsername] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    if (!getToken()) {
      setChecking(false);
      return;
    }
    api
      .get<{ username: string }>("/api/auth/me")
      .then((me) => setUsername(me.username))
      .catch(() => setUsername(null))
      .finally(() => setChecking(false));
  }, []);

  if (checking) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner />
      </div>
    );
  }
  if (!username) return <Navigate to="/login" replace />;
  return (
    <Layout username={username}>
      <Router />
    </Layout>
  );
}

function Router() {
  return (
    <Routes>
      <Route path="/" element={<Dashboard />} />
      <Route path="/devices" element={<Devices />} />
      <Route path="/devices/:id" element={<DeviceDetail />} />
      <Route path="/schedules" element={<Schedules />} />
      <Route path="/audit" element={<Audit />} />
      <Route path="/about" element={<About />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/*" element={<Shell />} />
      </Routes>
    </HashRouter>
  );
}
