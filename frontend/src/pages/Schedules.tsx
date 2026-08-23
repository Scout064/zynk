import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import type { Device, Schedule } from "../api/types";
import { Badge, Button, Card, EmptyState, Input, Select, Spinner, Toggle } from "../components/ui";
import { PageHeader } from "../components/Layout";

const PRESETS: { label: string; cron: string }[] = [
  { label: "Every hour", cron: "0 * * * *" },
  { label: "Every 6 hours", cron: "0 */6 * * *" },
  { label: "Daily at 02:00", cron: "0 2 * * *" },
  { label: "Weekly (Sun 03:00)", cron: "0 3 * * 0" },
];

interface FormState {
  name: string;
  cron: string;
  scope: "all" | "devices" | "tags";
  targets: string[];
  enabled: boolean;
}

const EMPTY: FormState = { name: "", cron: "0 2 * * *", scope: "all", targets: [], enabled: true };

export default function Schedules() {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState<Schedule | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [s, d] = await Promise.all([
        api.get<Schedule[]>("/api/schedules"),
        api.get<Device[]>("/api/devices"),
      ]);
      setSchedules(s);
      setDevices(d);
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

  function openEdit(s: Schedule) {
    setEditing(s);
    setForm({ name: s.name, cron: s.cron, scope: s.scope, targets: s.targets, enabled: s.enabled });
    setError("");
    setShowForm(true);
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (editing) await api.put(`/api/schedules/${editing.id}`, form);
      else await api.post("/api/schedules", form);
      setShowForm(false);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(s: Schedule) {
    if (!window.confirm(`Delete schedule "${s.name}"?`)) return;
    await api.del(`/api/schedules/${s.id}`);
    await load();
  }

  async function runNow(s: Schedule) {
    await api.post(`/api/schedules/${s.id}/run`);
    await load();
  }

  const allTags = [...new Set(devices.flatMap((d) => d.tags))];

  return (
    <>
      <PageHeader
        title="Schedules"
        actions={
          <Button variant="primary" onClick={openCreate}>
            + Add Schedule
          </Button>
        }
      />

      {showForm && (
        <Card className="mb-6 p-5">
          <h2 className="mb-4 font-medium">{editing ? `Edit "${editing.name}"` : "Add Schedule"}</h2>
          <form onSubmit={onSubmit} className="grid gap-4 md:grid-cols-2">
            <div>
              <label className="mb-1 block text-sm text-zinc-400">Name</label>
              <Input value={form.name} onChange={(v) => setForm({ ...form, name: v })} required />
            </div>
            <div>
              <label className="mb-1 block text-sm text-zinc-400">Frequency Preset</label>
              <Select
                value=""
                onChange={(v) => v && setForm({ ...form, cron: v })}
                options={[
                  { value: "", label: "Choose a preset…" },
                  ...PRESETS.map((p) => ({ value: p.cron, label: `${p.label} (${p.cron})` })),
                ]}
              />
            </div>
            <div>
              <label className="mb-1 block text-sm text-zinc-400">Cron Expression (UTC)</label>
              <Input value={form.cron} onChange={(v) => setForm({ ...form, cron: v })} required />
            </div>
            <div>
              <label className="mb-1 block text-sm text-zinc-400">Scope</label>
              <Select
                value={form.scope}
                onChange={(v) => setForm({ ...form, scope: v as FormState["scope"], targets: [] })}
                options={[
                  { value: "all", label: "All enabled devices" },
                  { value: "devices", label: "Specific devices" },
                  { value: "tags", label: "By tag" },
                ]}
              />
            </div>
            {form.scope !== "all" && (
              <div className="md:col-span-2">
                <label className="mb-1 block text-sm text-zinc-400">
                  {form.scope === "devices" ? "Devices" : "Tags"}
                </label>
                <div className="flex flex-wrap gap-2 rounded-lg border border-zinc-700 bg-zinc-900 p-2">
                  {form.scope === "devices"
                    ? devices.map((d) => (
                        <button
                          type="button"
                          key={d.id}
                          onClick={() =>
                            setForm((f) => ({
                              ...f,
                              targets: f.targets.includes(d.id)
                                ? f.targets.filter((t) => t !== d.id)
                                : [...f.targets, d.id],
                            }))
                          }
                          className={`rounded-md border px-2 py-1 text-xs ${chipCls(form.targets.includes(d.id))}`}
                        >
                          {d.name}
                        </button>
                      ))
                    : allTags.map((t) => (
                        <button
                          type="button"
                          key={t}
                          onClick={() =>
                            setForm((f) => ({
                              ...f,
                              targets: f.targets.includes(t)
                                ? f.targets.filter((x) => x !== t)
                                : [...f.targets, t],
                            }))
                          }
                          className={`rounded-md border px-2 py-1 text-xs ${chipCls(form.targets.includes(t))}`}
                        >
                          {t}
                        </button>
                      ))}
                </div>
              </div>
            )}
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
                {busy ? "Saving…" : editing ? "Save Changes" : "Add Schedule"}
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
      ) : schedules.length === 0 ? (
        <Card>
          <EmptyState
            title="No schedules configured"
            hint="Automate config backups with a cron schedule."
          />
        </Card>
      ) : (
        <Card>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-xs uppercase tracking-wide text-zinc-500">
                <th className="px-5 py-3">Name</th>
                <th className="px-5 py-3">Cron (UTC)</th>
                <th className="px-5 py-3">Scope</th>
                <th className="px-5 py-3">Next Run</th>
                <th className="px-5 py-3">Last Run</th>
                <th className="px-5 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {schedules.map((s) => (
                <tr key={s.id} className="hover:bg-zinc-800/30">
                  <td className="px-5 py-3">
                    <span className="font-medium">{s.name}</span>{" "}
                    {s.enabled ? <Badge tone="green">on</Badge> : <Badge tone="neutral">off</Badge>}
                  </td>
                  <td className="px-5 py-3 font-mono text-xs">{s.cron}</td>
                  <td className="px-5 py-3 text-zinc-400">{describeScope(s)}</td>
                  <td className="px-5 py-3 text-zinc-400">
                    {s.next_run ? new Date(s.next_run).toLocaleString() : "—"}
                  </td>
                  <td className="px-5 py-3 text-zinc-400">
                    {s.last_run ? new Date(s.last_run).toLocaleString() : "—"}
                  </td>
                  <td className="px-5 py-3">
                    <div className="flex justify-end gap-1">
                      <Button variant="ghost" onClick={() => runNow(s)}>
                        Run Now
                      </Button>
                      <Button variant="ghost" onClick={() => openEdit(s)}>
                        Edit
                      </Button>
                      <Button variant="ghost" className="text-red-400 hover:text-red-300" onClick={() => onDelete(s)}>
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

function chipCls(on: boolean): string {
  return on
    ? "border-indigo-500 bg-indigo-950/50 text-indigo-300"
    : "border-zinc-700 bg-zinc-800 text-zinc-400";
}

function describeScope(s: Schedule): string {
  if (s.scope === "all") return "All devices";
  if (s.scope === "tags") return `Tags: ${s.targets.join(", ") || "—"}`;
  return `${s.targets.length} device(s)`;
}
