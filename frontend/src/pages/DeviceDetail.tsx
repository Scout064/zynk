import { useCallback, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { Device, Snapshot } from "../api/types";
import { Badge, Button, Card, EmptyState, Spinner, StatusDot } from "../components/ui";
import { PageHeader } from "../components/Layout";
import { familyBadgeTone } from "./Devices";

function DiffView({ diff }: { diff: string }) {
  const lines = diff.split("\n");
  return (
    <pre className="overflow-x-auto rounded-lg bg-zinc-950 p-4 text-xs leading-relaxed">
      {lines.map((ln, i) => {
        const cls = ln.startsWith("+++") || ln.startsWith("---")
          ? "text-indigo-300"
          : ln.startsWith("@@")
            ? "text-amber-400"
            : ln.startsWith("+")
              ? "text-emerald-400"
              : ln.startsWith("-")
                ? "text-red-400"
                : "text-zinc-400";
        return (
          <div key={i} className={cls}>
            {ln || "\u00a0"}
          </div>
        );
      })}
    </pre>
  );
}

export default function DeviceDetail() {
  const { id } = useParams<{ id: string }>();
  const [device, setDevice] = useState<Device | null>(null);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [diff, setDiff] = useState<string | null>(null);
  const [view, setView] = useState<{ snap: Snapshot; text: string } | null>(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    if (!id) return;
    const [d, snaps] = await Promise.all([
      api.get<Device>(`/api/devices/${id}`),
      api.get<Snapshot[]>(`/api/devices/${id}/snapshots`),
    ]);
    setDevice(d);
    setSnapshots(snaps);
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  function toggle(id: string) {
    setDiff(null);
    setView(null);
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id].slice(-2)
    );
  }

  async function pull() {
    if (!id) return;
    setBusy("pull");
    setMessage("");
    try {
      const res = await api.post<{ saved: boolean; message: string }>(`/api/devices/${id}/pull`);
      setMessage(res.saved ? "New snapshot saved." : `Pull OK — config unchanged (${res.message}).`);
      await load();
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Pull failed");
    } finally {
      setBusy("");
    }
  }

  async function checkNow() {
    if (!id) return;
    await api.post(`/api/devices/${id}/check`);
    await load();
  }

  async function showDiff() {
    if (selected.length !== 2) return;
    setDiff(await api.get<string>(`/api/diff?a=${selected[1]}&b=${selected[0]}`));
  }

  async function viewSnapshot(s: Snapshot) {
    setSelected([]);
    setDiff(null);
    const text = await api.get<string>(`/api/snapshots/${s.id}`);
    setView({ snap: s, text });
  }

  async function revert(s: Snapshot) {
    if (
      !window.confirm(
        `DANGER: This will overwrite the device's current configuration with the snapshot from ${new Date(s.ts).toLocaleString()}.\n\nThe device will be contacted immediately. Continue?`
      )
    )
      return;
    setBusy("revert");
    setMessage("");
    try {
      const res = await api.post<{ message: string }>(`/api/snapshots/${s.id}/revert`, {
        confirm: true,
      });
      setMessage(`Revert applied. ${res.message}`);
      await load();
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Revert failed");
    } finally {
      setBusy("");
    }
  }

  if (!device) {
    return (
      <div className="flex justify-center py-24">
        <Spinner />
      </div>
    );
  }

  return (
    <>
      <PageHeader
        title={device.name}
        actions={
          <>
            <Button onClick={checkNow}>Check Status</Button>
            <Button variant="primary" onClick={pull} disabled={busy === "pull"}>
              {busy === "pull" ? "Pulling…" : "Pull Config Now"}
            </Button>
          </>
        }
      />
      <Card className="mb-6 p-5">
        <div className="flex flex-wrap items-center gap-x-8 gap-y-2 text-sm">
          <span className="flex items-center gap-2">
            <StatusDot reachable={device.status?.reachable ?? null} />
            {device.status?.reachable === true
              ? `Online · ${device.status.latency_ms?.toFixed(0)} ms`
              : device.status?.reachable === false
                ? "Offline"
                : "Unknown"}
          </span>
          <span className="text-zinc-400">
            {device.host}:{device.port} · {device.username}
          </span>
          <Badge tone={familyBadgeTone(device.family)}>
            {device.family === "zld_firewall" ? "firewall (ZLD, EOL)" : device.family}
          </Badge>
          {device.model && <span className="text-zinc-400">{device.model}</span>}
          {device.tags.map((t) => (
            <Badge key={t}>{t}</Badge>
          ))}
          <a href={`/api/devices/${device.id}/export`} className="ml-auto text-xs text-indigo-400 hover:text-indigo-300">
            Export history (zip)
          </a>
        </div>
        {message && (
          <p className="mt-3 rounded-lg border border-indigo-900 bg-indigo-950/40 px-3 py-2 text-sm text-indigo-300">
            {message}
          </p>
        )}
      </Card>

      <Card className="mb-6">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-800 px-5 py-3">
          <h2 className="font-medium">Configuration History</h2>
          <div className="flex items-center gap-2">
            <span className="text-xs text-zinc-500">
              {selected.length === 2 ? "Two selected" : `Select two to diff (${selected.length}/2)`}
            </span>
            <Button onClick={showDiff} disabled={selected.length !== 2}>
              Show Diff
            </Button>
          </div>
        </div>
        {snapshots.length === 0 ? (
          <EmptyState title="No snapshots yet" hint='Use "Pull Config Now" to create the first backup.' />
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-xs uppercase tracking-wide text-zinc-500">
                <th className="px-5 py-3"></th>
                <th className="px-5 py-3">Timestamp</th>
                <th className="px-5 py-3">Source</th>
                <th className="px-5 py-3">Hash</th>
                <th className="px-5 py-3">Size</th>
                <th className="px-5 py-3">Git</th>
                <th className="px-5 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/70">
              {snapshots.map((s) => (
                <tr
                  key={s.id}
                  className={`cursor-pointer hover:bg-zinc-800/30 ${selected.includes(s.id) ? "bg-indigo-950/30" : ""}`}
                  onClick={() => toggle(s.id)}
                >
                  <td className="px-5 py-2.5">
                    <input
                      type="checkbox"
                      checked={selected.includes(s.id)}
                      onChange={() => toggle(s.id)}
                      onClick={(e) => e.stopPropagation()}
                    />
                  </td>
                  <td className="px-5 py-2.5">{new Date(s.ts).toLocaleString()}</td>
                  <td className="px-5 py-2.5">
                    <Badge tone={s.source === "manual" ? "indigo" : s.source === "scheduled" ? "green" : "amber"}>
                      {s.source}
                    </Badge>
                  </td>
                  <td className="px-5 py-2.5 font-mono text-xs text-zinc-500">{s.config_hash.slice(0, 12)}</td>
                  <td className="px-5 py-2.5 text-zinc-400">{(s.size_bytes / 1024).toFixed(1)} KB</td>
                  <td className="px-5 py-2.5 font-mono text-xs text-zinc-500">
                    {s.git_commit ? s.git_commit.slice(0, 7) : "—"}
                  </td>
                  <td className="px-5 py-2.5">
                    <div className="flex justify-end gap-1" onClick={(e) => e.stopPropagation()}>
                      <Button variant="ghost" onClick={() => viewSnapshot(s)}>
                        View
                      </Button>
                      <a href={`/api/snapshots/${s.id}/download`}>
                        <Button variant="ghost">Download</Button>
                      </a>
                      <Button
                        variant="ghost"
                        className="text-red-400 hover:text-red-300"
                        disabled={busy === "revert"}
                        onClick={() => revert(s)}
                      >
                        Revert
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {diff !== null && (
        <Card className="mb-6 p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-medium">Diff (older → newer)</h2>
            <Button variant="ghost" onClick={() => setDiff(null)}>
              Close
            </Button>
          </div>
          {diff ? (
            <DiffView diff={diff} />
          ) : (
            <p className="text-sm text-zinc-500">Configurations are identical.</p>
          )}
        </Card>
      )}

      {view && (
        <Card className="mb-6 p-5">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-medium">
              Snapshot {new Date(view.snap.ts).toLocaleString()} ({view.snap.source})
            </h2>
            <Button variant="ghost" onClick={() => setView(null)}>
              Close
            </Button>
          </div>
          <pre className="max-h-[32rem] overflow-auto rounded-lg bg-zinc-950 p-4 text-xs leading-relaxed text-zinc-300">
            {view.text}
          </pre>
        </Card>
      )}
    </>
  );
}
