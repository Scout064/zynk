export type Family = "switch" | "firewall" | "zld_firewall" | "ap";

export interface Device {
  id: string;
  name: string;
  host: string;
  port: number;
  family: Family;
  model: string;
  username: string;
  tags: string[];
  enabled: boolean;
  notes: string;
  snapshot_count: number;
  last_snapshot_ts: string | null;
  status: { reachable: boolean; latency_ms: number | null; last_checked: string } | null;
}

export interface Snapshot {
  id: string;
  device_id: string;
  ts: string;
  source: string;
  config_hash: string;
  size_bytes: number;
  git_commit: string | null;
  message: string;
}

export interface Schedule {
  id: string;
  name: string;
  cron: string;
  scope: "all" | "devices" | "tags";
  targets: string[];
  enabled: boolean;
  last_run: string | null;
  next_run: string | null;
}

export interface StatusSummary {
  online: number;
  offline: number;
  interval_seconds: number;
  devices: {
    device_id: string;
    name: string;
    family: Family;
    enabled: boolean;
    reachable: boolean | null;
    latency_ms: number | null;
    last_checked: string | null;
  }[];
}

export interface AuditEntry {
  id: string;
  ts: string;
  actor: string;
  action: string;
  target: string;
  detail: string;
  ok: boolean;
}

export interface AboutInfo {
  name: string;
  version: string;
  python_version: string;
  license: string;
  repository: string;
  api_docs: string;
  started_at: string | null;
  uptime_seconds: number | null;
  stats: {
    devices: number;
    devices_enabled: number;
    snapshots: number;
    schedules: number;
    audit_entries: number;
  };
  families: {
    family: string;
    label: string;
    platform: string;
    verified_models: string;
    config_pull: string;
    revert_supported: boolean;
    revert_note: string;
    eol: boolean;
  }[];
}
