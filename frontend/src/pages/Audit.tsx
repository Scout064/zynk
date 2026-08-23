import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { AuditEntry } from "../api/types";
import { Badge, Card, EmptyState, Spinner } from "../components/ui";
import { PageHeader } from "../components/Layout";

export default function Audit() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setEntries(await api.get<AuditEntry[]>("/api/audit?limit=200"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <>
      <PageHeader title="Audit Log" />
      {loading ? (
        <div className="flex justify-center py-24">
          <Spinner />
        </div>
      ) : entries.length === 0 ? (
        <Card>
          <EmptyState title="No audit entries" />
        </Card>
      ) : (
        <Card>
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-xs uppercase tracking-wide text-zinc-500">
                <th className="px-5 py-3">Time</th>
                <th className="px-5 py-3">Actor</th>
                <th className="px-5 py-3">Action</th>
                <th className="px-5 py-3">Target</th>
                <th className="px-5 py-3">Detail</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {entries.map((e) => (
                <tr key={e.id} className="hover:bg-zinc-800/30">
                  <td className="whitespace-nowrap px-5 py-2.5 text-zinc-400">
                    {new Date(e.ts).toLocaleString()}
                  </td>
                  <td className="px-5 py-2.5">{e.actor}</td>
                  <td className="px-5 py-2.5">
                    <Badge tone={e.ok ? "green" : "red"}>{e.action}</Badge>
                  </td>
                  <td className="px-5 py-2.5">{e.target}</td>
                  <td className="px-5 py-2.5 text-zinc-500">{e.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
