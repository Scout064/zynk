import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { AuditEntry, StatusSummary } from "../api/types";
import { Badge, Card, EmptyState, Spinner, StatusDot } from "../components/ui";
import { PageHeader } from "../components/Layout";

function ageLabel(iso: string | null): string {
  if (!iso) return "never checked";
  const secs = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  return `${Math.floor(secs / 3600)}h ago`;
}

export default function Dashboard() {
  const [summary, setSummary] = useState<StatusSummary | null>(null);
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [s, a] = await Promise.all([api.get<StatusSummary>("/api/status"), api.get<AuditEntry[]>("/api/audit?limit=8")]);
      setSummary(s);
      setAudit(a);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, [load]);

  if (loading) {
    return (
      <div className="flex justify-center py-24">
        <Spinner />
      </div>
    );
  }

  const intervalMin = summary ? Math.round(summary.interval_seconds / 60) : 5;

  const stats = [
    { label: "Devices", value: summary?.devices.length ?? 0, tone: "text-zinc-100" },
    { label: "Online", value: summary?.online ?? 0, tone: "text-emerald-400" },
    { label: "Offline", value: summary?.offline ?? 0, tone: "text-red-400" },
  ];

  return (
    <>
      <PageHeader title="Dashboard" />
      <div className="mb-8 grid grid-cols-3 gap-4">
        {stats.map((s) => (
          <Card key={s.label} className="p-5">
            <p className="text-sm text-zinc-500">{s.label}</p>
            <p className={`mt-1 text-3xl font-semibold ${s.tone}`}>{s.value}</p>
          </Card>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <div className="flex items-center justify-between border-b border-zinc-800 px-5 py-3">
            <h2 className="font-medium">Device Status</h2>
            <Link to="/devices" className="text-xs text-indigo-400 hover:text-indigo-300">
              Manage →
            </Link>
          </div>
          {summary && summary.devices.length > 0 ? (
            <ul className="divide-y divide-zinc-800/70">
              {summary.devices.map((d) => (
                <li key={d.device_id} className="flex items-center gap-3 px-5 py-3">
                  <StatusDot reachable={d.reachable} />
                  <div className="min-w-0 flex-1">
                    <Link to={`/devices/${d.device_id}`} className="truncate font-medium hover:text-indigo-300">
                      {d.name}
                    </Link>
                    <p className="truncate text-xs text-zinc-500">
                      {d.family} · {d.enabled ? "enabled" : "disabled"} · checked{" "}
                      {ageLabel(d.last_checked)}
                    </p>
                  </div>
                  {d.latency_ms !== null && d.reachable && (
                    <span className="text-xs text-zinc-500">{d.latency_ms.toFixed(0)} ms</span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title="No devices yet"
              hint="Add your first Zyxel device to start backing up configurations."
            />
          )}
          <p className="border-t border-zinc-800 px-5 py-2.5 text-xs text-zinc-500">
            Status is probed automatically every {intervalMin} minute{intervalMin === 1 ? "" : "s"}
            ; each device keeps its last known state until the next check.
          </p>
        </Card>

        <Card>
          <div className="flex items-center justify-between border-b border-zinc-800 px-5 py-3">
            <h2 className="font-medium">Recent Activity</h2>
            <Link to="/audit" className="text-xs text-indigo-400 hover:text-indigo-300">
              Full log →
            </Link>
          </div>
          {audit.length > 0 ? (
            <ul className="divide-y divide-zinc-800/70">
              {audit.map((e) => (
                <li key={e.id} className="flex items-start gap-3 px-5 py-2.5">
                  <Badge tone={e.ok ? "green" : "red"}>{e.action}</Badge>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm">
                      <span className="font-medium">{e.target}</span>{" "}
                      <span className="text-zinc-500">{e.detail}</span>
                    </p>
                    <p className="text-xs text-zinc-600">
                      {new Date(e.ts).toLocaleString()} · {e.actor}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No activity yet" />
          )}
        </Card>
      </div>
    </>
  );
}
