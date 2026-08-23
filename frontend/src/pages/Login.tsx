import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { login, setToken } from "../api/client";
import { Button, Input } from "../components/ui";

export default function Login() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const token = await login(username, password);
      setToken(token);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-zinc-950 p-4">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-3">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-indigo-600 text-xl font-bold text-white">
            Z
          </span>
          <div className="text-center">
            <h1 className="text-2xl font-semibold tracking-tight">Zynk</h1>
            <p className="text-sm text-zinc-500">Zyxel Config Backup &amp; Management</p>
          </div>
        </div>
        <form
          onSubmit={onSubmit}
          className="space-y-4 rounded-2xl border border-zinc-800 bg-zinc-900/70 p-6 shadow-xl"
        >
          <div>
            <label className="mb-1 block text-sm text-zinc-400">Username</label>
            <Input value={username} onChange={setUsername} required autoComplete="username" />
          </div>
          <div>
            <label className="mb-1 block text-sm text-zinc-400">Password</label>
            <Input
              value={password}
              onChange={setPassword}
              type="password"
              required
              autoComplete="current-password"
            />
          </div>
          {error && (
            <p className="rounded-lg border border-red-900 bg-red-950/60 px-3 py-2 text-sm text-red-400">
              {error}
            </p>
          )}
          <Button type="submit" variant="primary" disabled={busy} className="w-full">
            {busy ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </div>
    </div>
  );
}
