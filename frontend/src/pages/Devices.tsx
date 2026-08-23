import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { Device, Family } from "../api/types";
import { Badge, Button, Card, EmptyState, Input, Select, Spinner, StatusDot, Toggle } from "../components/ui";
import { PageHeader } from "../components/Layout";

const FAMILY_LABEL: Record<Family, string> = {
  switch: "Switch (GS/XGS/XS/CX)",
  firewall: "Firewall (USG FLEX H, uOS)",
  zld_firewall: "Firewall (USG/ATP, ZLD) — End of Life",
  ap: "Access Point (NWA/WAX/WBE)",
};

export function familyBadgeTone(family: Family): "indigo" | "amber" | "green" | "red" {
  if (family === "switch") return "indigo";
  if (family === "firewall" || family === "zld_firewall") return "amber";
  return "green";
}

interface FormState {
  name: string;
  host: string;
  port: string;
  family: Family;
  model: string;
  username: string;
  password: string;
  tags: string;
  enabled: boolean;
  notes: string;
}

const EMPTY: FormState = {
  name: "",
  host: "",
  port: "22",
  family: "switch",
  model: "",
  username: "admin",
  password: "",
  tags: "",
  enabled: true,
  notes: "",
};

export default function Devices() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Device | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [testResult, setTestResult] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    try {
      setDevices(await api.get<Device[]>("/api/devices"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function openCreate() {
    setEditing(null);
    setForm(EMPTY);
    setError("");
    setShowForm(true);
  }

  function openEdit(d: Device) {
    setEditing(d);
    setForm({
      name: d.name,
      host: d.host,
      port: String(d.port),
      family: d.family,
      model: d.model,
      username: d.username,
      password: "",
      tags: d.tags.join(", "),
      enabled: d.enabled,
      notes: d.notes,
    });
    setError("");
    setShowForm(true);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const payload = {
      ...form,
      port: Number(form.port),
      tags: form.tags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean),
    };
    try {
      if (editing) {
        await api.put(`/api/devices/${editing.id}`, payload);
      } else {
        await api.post("/api/devices", payload);
      }
      setShowForm(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(d: Device) {
    if (!window.confirm(`Delete device "${d.name}" and all its snapshots?`)) return;
    await api.del(`/api/devices/${d.id}`);
    await load();
  }

  async function onTest(d: Device) {
    setTestResult((r) => ({ ...r, [d.id]: "testing…" }));
    try {
      const res = await api.post<{
        ok: boolean;
        message: string;
        tftp?: { ok: boolean; message: string } | null;
      }>(`/api/devices/${d.id}/test`);
      let text = res.message;
      if (res.tftp) {
        text += ` — ${res.tftp.message}`;
      }
      setTestResult((r) => ({ ...r, [d.id]: text }));
    } catch (err) {
      setTestResult((r) => ({ ...r, [d.id]: err instanceof ApiError ? err.message : "failed" }));
    }
  }

  return (
    <>
      <PageHeader
        title="Devices"
        actions={
          <Button variant="primary" onClick={openCreate}>
            + Add Device
          </Button>
        }
      />

      {showForm && (
        <Card className="mb-6 p-5">
          <h2 className="mb-4 font-medium">{editing ? `Edit "${editing.name}"` : "Add Device"}</h2>
          <form onSubmit={onSubmit} className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm text-zinc-400">Name</label>
              <Input value={form.name} onChange={(v) => setForm({ ...form, name: v })} required />
            </div>
            <div>
              <label className="mb-1 block text-sm text-zinc-400">Host / IP</label>
              <Input value={form.host} onChange={(v) => setForm({ ...form, host: v })} required />
            </div>
            <div>
              <label className="mb-1 block text-sm text-zinc-400">SSH Port</label>
              <Input value={form.port} onChange={(v) => setForm({ ...form, port: v })} type="number" min={1} />
            </div>
            <div>
              <label className="mb-1 block text-sm text-zinc-400">Device Family</label>
              <Select
                value={form.family}
                onChange={(v) => setForm({ ...form, family: v as Family })}
                options={(Object.keys(FAMILY_LABEL) as Family[]).map((f) => ({
                  value: f,
                  label: FAMILY_LABEL[f],
                }))}
              />
            </div>
            <div>
              <label className="mb-1 block text-sm text-zinc-400">Model</label>
              <Input
                value={form.model}
                onChange={(v) => setForm({ ...form, model: v })}
                placeholder="e.g. XS1930-12HP"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm text-zinc-400">SSH Username</label>
              <Input value={form.username} onChange={(v) => setForm({ ...form, username: v })} required />
            </div>
            <div>
              <label className="mb-1 block text-sm text-zinc-400">
                SSH Password {editing && <span className="text-zinc-600">(leave empty to keep)</span>}
              </label>
              <Input
                value={form.password}
                onChange={(v) => setForm({ ...form, password: v })}
                type="password"
                placeholder="stored encrypted"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm text-zinc-400">Tags (comma-separated)</label>
              <Input value={form.tags} onChange={(v) => setForm({ ...form, tags: v })} placeholder="core, office" />
            </div>
            <div className="md:col-span-2">
              <label className="mb-1 block text-sm text-zinc-400">Notes</label>
              <Input value={form.notes} onChange={(v) => setForm({ ...form, notes: v })} />
            </div>
            <div className="flex items-center">
              <Toggle
                checked={form.enabled}
                onChange={(v) => setForm({ ...form, enabled: v })}
                label="Enabled"
              />
            </div>
            <div className="flex items-end justify-end gap-2 md:col-span-2">
              <Button onClick={() => setShowForm(false)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={busy}>
                {busy ? "Saving…" : editing ? "Save Changes" : "Add Device"}
              </Button>
            </div>
          </form>
          {error && (
            <p className="mt-3 rounded-lg border border-red-900 bg-red-950/60 px-3 py-2 text-sm text-red-400">
              {error}
            </p>
          )}
        </Card>
      )}

      {loading ? (
        <div className="flex justify-center py-24">
          <Spinner />
        </div>
      ) : devices.length === 0 ? (
        <Card>
          <EmptyState
            title="No devices configured"
            hint="Add a switch, firewall or access point to begin."
          />
        </Card>
      ) : (
        <Card>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-xs uppercase tracking-wide text-zinc-500">
                <th className="px-5 py-3">Status</th>
                <th className="px-5 py-3">Device</th>
                <th className="px-5 py-3">Family</th>
                <th className="px-5 py-3">Snapshots</th>
                <th className="px-5 py-3">Last Backup</th>
                <th className="px-5 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {devices.map((d) => (
                <tr key={d.id} className="hover:bg-zinc-800/30">
                  <td className="px-5 py-3">
                    <StatusDot reachable={d.status?.reachable ?? null} />
                  </td>
                  <td className="px-5 py-3">
                    <Link to={`/devices/${d.id}`} className="font-medium hover:text-indigo-300">
                      {d.name}
                    </Link>
                    <p className="text-xs text-zinc-500">
                      {d.host}:{d.port}
                      {d.model && ` · ${d.model}`}
                    </p>
                    {testResult[d.id] && (
                      <p className={`mt-1 text-xs ${testResult[d.id] === "testing…" ? "text-zinc-500" : "text-indigo-300"}`}>
                        {testResult[d.id]}
                      </p>
                    )}
                  </td>
                  <td className="px-5 py-3">
                    <Badge tone={familyBadgeTone(d.family)}>
                      {d.family === "zld_firewall" ? "firewall (ZLD, EOL)" : d.family}
                    </Badge>
                  </td>
                  <td className="px-5 py-3">{d.snapshot_count}</td>
                  <td className="px-5 py-3 text-zinc-400">
                    {d.last_snapshot_ts ? new Date(d.last_snapshot_ts).toLocaleString() : "—"}
                  </td>
                  <td className="px-5 py-3">
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" onClick={() => onTest(d)}>
                        Test
                      </Button>
                      <Button variant="ghost" onClick={() => openEdit(d)}>
                        Edit
                      </Button>
                      <Button variant="ghost" className="text-red-400 hover:text-red-300" onClick={() => onDelete(d)}>
                        Delete
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
