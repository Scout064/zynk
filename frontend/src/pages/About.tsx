import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { AboutInfo } from "../api/types";
import pkg from "../../package.json";
import { Badge, Card, Spinner } from "../components/ui";
import { PageHeader } from "../components/Layout";

function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m ${Math.floor(seconds % 60)}s`;
}

const TECH_STACK = [
  ["Backend", "Python 3.12 · FastAPI · SQLAlchemy · APScheduler"],
  ["SSH", "Paramiko (ANSI-sanitized interactive shell)"],
  ["Storage", "SQLite + git-backed config repository"],
  ["Frontend", "React 19 · TypeScript · Vite · Tailwind CSS 4"],
  ["Deployment", "Docker / docker-compose"],
];

export default function About() {
  const [info, setInfo] = useState<AboutInfo | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setInfo(await api.get<AboutInfo>("/api/about"));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load about info");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 30000); // keep uptime fresh
    return () => clearInterval(t);
  }, [load]);

  if (error) {
    return (
      <>
        <PageHeader title="About" />
        <Card className="p-6 text-sm text-red-400">{error}</Card>
      </>
    );
  }
  if (!info) {
    return (
      <div className="flex justify-center py-24">
        <Spinner />
      </div>
    );
  }

  return (
    <>
      <PageHeader title="About" />

      {/* Hero */}
      <Card className="mb-6 overflow-hidden">
        <div className="flex flex-col items-center gap-4 bg-gradient-to-b from-indigo-950/40 to-transparent px-6 py-10 text-center">
          <span className="flex h-16 w-16 items-center justify-center rounded-2xl bg-indigo-600 text-2xl font-bold text-white shadow-lg">
            Z
          </span>
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">
              {info.name} <span className="text-zinc-500">v{info.version}</span>
            </h2>
            <p className="mt-1 text-sm text-zinc-400">
              Self-hosted Zyxel network device configuration backup &amp; management
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Badge tone="indigo">alpha</Badge>
            <Badge>backend v{info.version}</Badge>
            <Badge>frontend v{pkg.version}</Badge>
            <Badge>python {info.python_version}</Badge>
            <Badge tone="green">{info.license}</Badge>
          </div>
        </div>
      </Card>

      {/* Runtime + stats */}
      <div className="mb-6 grid gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <h3 className="mb-4 font-medium">Runtime</h3>
          <dl className="space-y-3 text-sm">
            <div className="flex justify-between gap-4">
              <dt className="text-zinc-500">Backend version</dt>
              <dd className="font-mono">v{info.version}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-zinc-500">Frontend version</dt>
              <dd className="font-mono">v{pkg.version}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-zinc-500">Python</dt>
              <dd className="font-mono">{info.python_version}</dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-zinc-500">Started at</dt>
              <dd>
                {info.started_at ? new Date(info.started_at).toLocaleString() : "—"}
              </dd>
            </div>
            <div className="flex justify-between gap-4">
              <dt className="text-zinc-500">Uptime</dt>
              <dd>
                {info.uptime_seconds !== null ? formatUptime(info.uptime_seconds) : "—"}
              </dd>
            </div>
          </dl>
        </Card>

        <Card className="p-5">
          <h3 className="mb-4 font-medium">This Instance</h3>
          <div className="grid grid-cols-2 gap-4">
            {[
              { label: "Devices", value: `${info.stats.devices} (${info.stats.devices_enabled} enabled)` },
              { label: "Snapshots", value: String(info.stats.snapshots) },
              { label: "Schedules", value: String(info.stats.schedules) },
              { label: "Audit entries", value: String(info.stats.audit_entries) },
            ].map((s) => (
              <div key={s.label} className="rounded-lg border border-zinc-800 bg-zinc-950/60 p-3">
                <p className="text-xs text-zinc-500">{s.label}</p>
                <p className="mt-1 text-lg font-semibold">{s.value}</p>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {/* Device support */}
      <Card className="mb-6">
        <div className="border-b border-zinc-800 px-5 py-3">
          <h3 className="font-medium">Supported Devices</h3>
          <p className="mt-0.5 text-xs text-zinc-500">
            Verified against the official Zyxel CLI reference guides
          </p>
        </div>
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-zinc-800 text-xs uppercase tracking-wide text-zinc-500">
              <th className="px-5 py-3">Family</th>
              <th className="px-5 py-3">Platform</th>
              <th className="px-5 py-3">Verified models</th>
              <th className="px-5 py-3">Config pull</th>
              <th className="px-5 py-3">Revert</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70">
            {info.families.map((f) => (
              <tr key={f.family}>
                <td className="px-5 py-3 font-medium">{f.label}</td>
                <td className="px-5 py-3">
                  <Badge tone="indigo">{f.platform}</Badge>
                </td>
                <td className="px-5 py-3 text-zinc-400">{f.verified_models}</td>
                <td className="px-5 py-3 font-mono text-xs text-zinc-400">{f.config_pull}</td>
                <td className="px-5 py-3">
                  {f.revert_supported ? (
                    <Badge tone="green">yes — {f.revert_note}</Badge>
                  ) : (
                    <Badge tone="amber">{f.revert_note}</Badge>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {/* Stack + links */}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card className="p-5">
          <h3 className="mb-4 font-medium">Tech Stack</h3>
          <dl className="space-y-2.5 text-sm">
            {TECH_STACK.map(([k, v]) => (
              <div key={k} className="flex justify-between gap-4">
                <dt className="shrink-0 text-zinc-500">{k}</dt>
                <dd className="text-right text-zinc-300">{v}</dd>
              </div>
            ))}
          </dl>
        </Card>

        <Card className="p-5">
          <h3 className="mb-4 font-medium">Links</h3>
          <ul className="space-y-2.5 text-sm">
            <li>
              <a
                href={info.repository}
                target="_blank"
                rel="noreferrer"
                className="text-indigo-400 hover:text-indigo-300"
              >
                GitHub repository ↗
              </a>
            </li>
            <li>
              <a
                href={info.api_docs}
                target="_blank"
                rel="noreferrer"
                className="text-indigo-400 hover:text-indigo-300"
              >
                REST API documentation (OpenAPI/Swagger) ↗
              </a>
            </li>
            <li>
              <a
                href="https://github.com/Scout064/zynk/blob/main/docs/DOCUMENTATION.md"
                target="_blank"
                rel="noreferrer"
                className="text-indigo-400 hover:text-indigo-300"
              >
                Full documentation ↗
              </a>
            </li>
          </ul>
          <p className="mt-5 border-t border-zinc-800 pt-4 text-xs leading-relaxed text-zinc-500">
            Alpha software — device drivers are tested against mocked SSH sessions;
            verify against your hardware before relying on it. Config revert is a
            destructive operation and is audited. Do not expose this app to the
            internet without TLS.
          </p>
        </Card>
      </div>
    </>
  );
}
