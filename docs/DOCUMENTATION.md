# Zynk Documentation

Complete documentation for Zynk — self-hosted Zyxel network configuration backup
and management. For a quick introduction see the [README](../README.md); for
step-by-step installation on Debian-based systems see
[INSTALL-DEBIAN.md](INSTALL-DEBIAN.md).

> Device CLI commands in §7 were verified against the official Zyxel CLI
> reference guides (kept locally in `CLI-GUIDE/`, not committed for copyright
> reasons).

**Table of contents**

1. [Overview](#1-overview)
2. [Installation](#2-installation)
3. [Configuration reference](#3-configuration-reference)
4. [First steps](#4-first-steps)
5. [Using the web UI](#5-using-the-web-ui)
6. [REST API reference](#6-rest-api-reference)
7. [Device drivers & CLI commands](#7-device-drivers--cli-commands)
8. [Architecture](#8-architecture)
9. [Data layout & backup/restore](#9-data-layout--backuprestore)
10. [Security model](#10-security-model)
11. [Troubleshooting](#11-troubleshooting)
12. [Limitations & roadmap](#12-limitations--roadmap)

---

## 1. Overview

Zynk connects to Zyxel network devices over SSH, pulls their running configuration,
stores every change as a versioned snapshot, and lets you browse, diff, export and
(where supported) restore configurations. It is a single-user/self-hosted tool for
home or small-business networks.

Core capabilities:

- **Inventory** — manage switches, firewalls and access points with per-device
  credentials (encrypted at rest).
- **Backup** — manual pulls from the UI or API; scheduled pulls via cron expressions.
- **Versioning** — each pull is hashed (SHA-256); unchanged configurations are not
  re-stored. Every stored snapshot is also committed to a git repository.
- **History** — unified diff between any two snapshots, snapshot viewer, per-snapshot
  download, full-history zip export.
- **Status** — periodic TCP probe of each device's SSH port with latency, shown on
  the dashboard.
- **Audit** — an append-only log of every login, pull, export, revert and inventory
  change (who / what / when / outcome).

## 2. Installation

### 2.1 Docker (recommended)

From a checkout of this repository (the image is built from the repo — clone
it first if you haven't):

```bash
git clone https://github.com/Scout064/zynk.git && cd zynk
cd docker
docker compose up -d --build
```

- The app is served at `http://localhost:8000` (UI and API on the same port).
- All persistent state lives in the mounted `data/` directory.
- Requires `git` inside the container — the provided image installs it.

### 2.2 From source

Prerequisites: Python 3.12+, Node.js 20+, git.

```bash
# Backend — run from backend/ (the project root has no pyproject.toml)
python3 -m venv .venv
cd backend
../.venv/bin/pip install -e ".[dev]"
../.venv/bin/python -m uvicorn app.main:app --reload   # http://localhost:8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev                              # http://localhost:5173, proxies /api -> :8000
```

For production-from-source: build the frontend (`npm run build`) and restart the
backend; it automatically serves `frontend/dist` at `/` when that directory exists.

### 2.3 Verify the install

```bash
curl http://localhost:8000/api/health
# {"status":"ok"}
```

## 3. Configuration reference

All settings are environment variables with the prefix `ZYNK_` (a `.env` file in
the working directory is also read).

| Variable | Default | Description |
|---|---|---|
| `ZYNK_DATA_DIR` | `./data` | Directory for the SQLite DB, git config repo and key files |
| `ZYNK_HOST` | `0.0.0.0` | Bind host (use `127.0.0.1` to restrict to localhost) |
| `ZYNK_PORT` | `8000` | Bind port |
| `ZYNK_INITIAL_ADMIN_PASSWORD` | *(random, printed once)* | Password for the `admin` user created on first start |
| `ZYNK_FORCE_ADMIN_RESET` | `false` | **Dev only:** reset the `admin` password on **every** startup (to `ZYNK_INITIAL_ADMIN_PASSWORD`, or a generated one printed to the log). Escape hatch for a lost local password — never enable in production. |
| `ZYNK_ACCESS_TOKEN_TTL_MINUTES` | `720` | JWT lifetime (12 h) |
| `ZYNK_STATUS_POLL_INTERVAL_SECONDS` | `300` | How often device status is probed (every 5 minutes by default) |
| `ZYNK_SSH_CONNECT_TIMEOUT_SECONDS` | `15` | SSH TCP/banner/auth timeout |
| `ZYNK_SSH_COMMAND_TIMEOUT_SECONDS` | `120` | Timeout per CLI command (config pulls can be large) |
| `ZYNK_TFTP_PUBLIC_ADDRESS` | *(auto-detect)* | IP address switches use to reach Zynk for TFTP restores. Auto-detection uses the routing source IP toward the device; in Docker bridge networking set this to the host's LAN IP. |
| `ZYNK_TFTP_PORT` | `69` | UDP port the TFTP server listens on for switch restores. Binding below 1024 requires root or `CAP_NET_BIND_SERVICE`. |
| `ZYNK_SWITCH_REBOOT_TIMEOUT_SECONDS` | `300` | How long a switch revert waits for the device to come back after `reload config` |

## 4. First steps

1. **Sign in.** On first start an `admin` user is created. Either set
   `ZYNK_INITIAL_ADMIN_PASSWORD` before the first launch, or read the generated
   password from the logs:
   ```bash
   docker logs zynk    # look for "created user 'admin' with generated password"
   ```
   Change the password immediately via `POST /api/auth/change-password` (§6.1).
2. **Add a device.** Devices → *+ Add Device*. Fill in name, host/IP, SSH port,
   family (switch / firewall / zld_firewall / ap), model, SSH credentials and
   optional tags.
3. **Test the connection.** Press *Test* on the device row. This performs a full
   SSH login and runs a harmless show command. Distinct failure reasons
   (auth failed / unreachable / timeout) are displayed.
4. **Pull the first config.** Open the device → *Pull Config Now*. The first
   snapshot is always stored; later identical pulls are skipped (dedup).
5. **Add a schedule** (optional). Schedules → *+ Add Schedule*, e.g. daily at
   02:00 UTC (`0 2 * * *`).

## 5. Using the web UI

### Dashboard
Counts of devices/online/offline, live device list with status dots and latency,
and the 8 most recent audit events. Refreshes every 30 s.

**Status semantics:** the backend probes every enabled device's SSH port every
5 minutes (`ZYNK_STATUS_POLL_INTERVAL_SECONDS`, default 300). Status is
persisted between checks — a device keeps its last known online/offline state
until the next check completes; the UI shows how long ago each device was last
checked. Use *Check Status* on the device page for an immediate probe.

All API timestamps are offset-aware UTC ISO-8601 (`…+00:00`); "checked Xs ago"
labels are computed in the browser from these.

### Devices
Full inventory table with status, snapshot count, last backup time and actions:
*Test* (SSH login check), *Edit*, *Delete* (removes the device **and all its
snapshots**), and links into the per-device history page. If any device has
tags, a filter bar above the table narrows the list by tag (click a tag chip
again to clear it); `GET /api/devices?tag=<name>` does the same via the API.

### Device detail
- Header card: status, host:port, family, model, tags, *Check Status*,
  *Pull Config Now*, *Export history (zip)*.
- **Configuration history table**: timestamp, source (manual / scheduled /
  post_revert), hash prefix, size, git commit, and per-row actions:
  - *View* — inline snapshot viewer.
  - *Download* — raw `.cfg` download.
  - *Revert* — destructive restore (confirmation required); only supported for
    access points in this alpha.
- **Diff**: check any two rows → *Show Diff* renders a colorized unified diff
  (older → newer).

### Schedules
Cron-based backup jobs (5-field crontab syntax, **UTC**). Scope can be
*all enabled devices*, a *device selection*, or *tag-based*. Shows next/last run
computed by the scheduler. *Run Now* triggers the job immediately.

### Audit Log
Every security-relevant event with actor, action, target, detail and success flag.

### About
Version information (backend/frontend/Python), instance uptime and usage stats,
supported device families with revert support, tech stack overview, and links to
the repository, API docs and full documentation. The current version is also
shown at the bottom of the sidebar.

## 6. REST API reference

Base URL: `http://<host>:8000/api`. Interactive docs (OpenAPI/Swagger) are served
by FastAPI at `/docs` (and ReDoc at `/redoc`, raw schema at `/openapi.json`).

> In dev mode (`npm run dev` on port 5173) the docs are reachable at
> `http://localhost:5173/docs` as well — the Vite dev server proxies `/docs`,
> `/redoc` and `/openapi.json` to the backend.

**Authentication** — all endpoints below (except `/health`) require a JWT:

```bash
# obtain a token (form-encoded)
curl -X POST http://localhost:8000/api/auth/token \
     -d "username=admin&password=<password>"
# use it
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/devices
```

Expired/invalid tokens yield `401`; the frontend then redirects to the login page.

### 6.1 Auth

| Method & Path | Body | Result |
|---|---|---|
| `POST /auth/token` | form: `username`, `password` | `{access_token, token_type, username}` |
| `GET /auth/me` | — | `{username, is_admin}` |
| `POST /auth/change-password` | `{current_password, new_password}` (min 8 chars) | `{ok: true}` |

### 6.2 Devices

| Method & Path | Body | Result |
|---|---|---|
| `GET /devices` | — | `DeviceOut[]` (incl. snapshot count, last snapshot, status); optional `?tag=<name>` filters by tag |
| `POST /devices` | `DeviceIn` | `201 DeviceOut` |
| `GET /devices/{id}` | — | `DeviceOut` |
| `PUT /devices/{id}` | `DeviceIn` | `DeviceOut` (empty `password` keeps stored credential) |
| `DELETE /devices/{id}` | — | `204` (cascades snapshots + status) |
| `POST /devices/{id}/test` | — | `{ok, message}` — full SSH login probe |
| `POST /devices/{id}/pull` | — | `{ok, saved, snapshot_id, hash, message}` |
| `POST /devices/{id}/check` | — | `{reachable, latency_ms, last_checked}` |

`DeviceIn`:
```json
{
  "name": "Core-SW",
  "host": "10.0.0.2",
  "port": 22,
  "family": "switch",
  "model": "XS1930-12HP",
  "username": "admin",
  "password": "…",
  "tags": ["core"],
  "enabled": true,
  "notes": ""
}
```

`family` must be `switch`, `firewall`, `zld_firewall` or `ap`; `password` is
write-only and
omitted from all responses.

### 6.3 Configs & snapshots

| Method & Path | Result |
|---|---|
| `GET /devices/{id}/snapshots` | `SnapshotOut[]` (newest first) |
| `GET /snapshots/{id}` | raw config text (`text/plain`) |
| `GET /snapshots/{id}/download` | config as attachment |
| `GET /diff?a=<id>&b=<id>` | unified diff text (a = older, b = newer) |
| `GET /devices/{id}/export` | zip of the device's full history |
| `POST /snapshots/{id}/revert` | destructive restore, body `{"confirm": true}` required |

`SnapshotOut`:
```json
{
  "id": "…",
  "device_id": "…",
  "ts": "2026-08-23T07:19:07.457633+00:00",
  "source": "manual",
  "config_hash": "sha256…",
  "size_bytes": 6918,
  "git_commit": "ab12cd3…",
  "message": "config changed"
}
```

`source` is `manual`, `scheduled` or `post_revert`. A successful revert triggers
an automatic confirmation pull (`post_revert`).

### 6.4 Schedules

| Method & Path | Body | Result |
|---|---|---|
| `GET /schedules` | — | `ScheduleOut[]` (incl. next_run) |
| `POST /schedules` | `ScheduleIn` | `201 ScheduleOut` |
| `PUT /schedules/{id}` | `ScheduleIn` | `ScheduleOut` |
| `DELETE /schedules/{id}` | — | `204` |
| `POST /schedules/{id}/run` | — | `{ok, last_run}` — trigger immediately |

`ScheduleIn`:
```json
{
  "name": "nightly",
  "cron": "0 2 * * *",
  "scope": "all",
  "targets": [],
  "enabled": true
}
```

`cron` is a 5-field crontab expression evaluated in **UTC**. `scope` is `all`,
`devices` (targets = device ids) or `tags` (targets = tag names).

### 6.5 Status & audit

| Method & Path | Result |
|---|---|
| `GET /about` | version, runtime (Python, uptime, started_at), instance stats and supported device families |
| `GET /status` | `{online, offline, interval_seconds, devices: [{device_id, name, family, enabled, reachable, latency_ms, last_checked}]}` — status persists between polls; `last_checked` (null = never) shows freshness |
| `GET /audit?limit=100` | audit entries newest-first (limit ≤ 500) |
| `GET /health` | `{status: "ok", version}` — unauthenticated liveness probe |

### 6.6 Error format

Failures return JSON `{"detail": "…"}`. Device-interaction failures are prefixed
with a failure kind in square brackets:

| Kind | Meaning |
|---|---|
| `[auth]` | SSH login rejected (wrong username/password) |
| `[unreachable]` | TCP/SSH connection failed (device off, wrong IP, firewall) |
| `[timeout]` | Connection or command timed out |
| `[unsupported]` | Operation not supported for this device family |
| `[failed]` | Device-side failure during an operation (e.g. FTP upload for revert) |

Pull/revert failures use HTTP `502` with the detailed message; validation errors
use `422`; missing resources `404`.

## 7. Device drivers & CLI commands

One driver per Zyxel product family, behind a common interface
(`connect()`, `get_config()`, `apply_config()`, `check_alive()`). Commands were
taken from the official CLI guides shipped in `CLI-GUIDE/` — do not change them
without checking the relevant guide.

| Family | Platform (per README) | Verified models (guide) | Config pull | Pager handling | Revert |
|---|---|---|---|---|---|
| `switch` | all ZyNOS and FaOS based switches | XS1930-12HP (V4.80–4.90), CX4800-56F (V1.00–5.00) | `show running-config` | plain form is unpaged; the paged variant is `show running-config page` — not used | **Supported.** Zynk serves the snapshot over TFTP (UDP 69, blksize negotiation per RFC 2348), stages it with `copy tftp config 1 <ip> <file>`, then applies it with `reload config 1` (confirmed `y`) — a warm reboot. Zynk waits for the SSH port to answer again before the confirmation pull. |
| `firewall` | all uOS based firewalls | USG FLEX 700H (V1.39) | **FTP download of `/conf/startup-config.conf`** (preferred — includes real secrets and is directly apply-able; requires the device FTP server: `vrf main ftp-server enabled true` + `commit`). CLI fallback: `show config running \| no-pager` (secrets are masked/hashed, tree format). | pager additionally disabled session-wide via `cliconfig pager enabled false` (CLI path) | **Supported (upload path + native format dry-run hardware-verified).** Native-format snapshots (FTP pulls) are uploaded as-is; tree-format (CLI-pulled) snapshots are converted to flat CLI script syntax first — the device's own apply-config-error.log shows the tree format is fully ignored — and carry masked secrets, so prefer FTP pulls before restoring. Upload goes to `/tmp` (passive mode; direct `/conf` STOR is firmware-blocked with 550) then FTP-renamed into `/conf`; validated with `cmd config-apply <file> option dry-run` (file name BEFORE `option`; applies NOTHING — verified `ok / message OK`); then `cmd config-apply <file>` — applies immediately **without a reboot**; the uploaded file is removed afterwards (`cmd config-delete`). SFTP/SCP on this firmware are unusable (garbage packets / nc-cli intercepts exec) and only serve as upload fallbacks. |
| `zld_firewall` | all ZLD based firewalls (ATP & USG ZyWALL) — **End of Life** | ATP800 (V4.10–5.42; the ZLD guide covers USG ZyWALL as well) | `show running-config` (at privilege prompt; driver sends `enable` if needed) | none documented for ZLD | **Supported.** FTP upload to `/conf/`, then `apply /conf/<file> ignore-error rollback` + `write` (single-line command). Requires FTP enabled on the device. |
| `ap` | ZyNOS based access points | WBE660S (V7.40) | `show running-config` (at enable prompt) | — | **Supported.** FTP upload to `/conf/`, then `apply running-config /conf/<file>` + `ignore error rollback` + `write`. Requires FTP enabled on the AP. |

> ⚠ **End of Life:** ZLD-based devices (USG & ATP series) are in End-of-Life
> state at Zyxel — no further firmware updates or support. `zld_firewall`
> support exists for existing installations; plan migration to a current
> platform (e.g. USG FLEX H / uOS).

Prompt patterns (used to detect command completion):

- Switch: `sysname#`, `sysname>`, `sysname(config)#`
- Firewall: `host>`, `host running config#`
- AP: `Router>`, `Router#`, `Router(config)#`; the driver sends `enable` when it
  lands in user mode.

Pulled output is normalized before storage: the CLI preamble
(`Building configuration…` / `Current configuration:`) is stripped — these are
CLI output lines, not config statements, and a config file containing them
would be rejected by the device on restore — as are pager remnants
(`-- more --`, `Press any key…`, lone `:` lines) and trailing whitespace; the
echoed command and trailing prompt are removed. The config header comment
lines (`; Product Name = …`) are kept — they are valid config comments and
Zynk parses the model from them for diagnostics. Snapshots stored before the
preamble fix are stripped again on read, so old snapshots also restore cleanly
(views/diffs of old snapshots show the stripped form as well; the next pull
of an unchanged config stores one re-normalized snapshot).

### Adding a new family/model

1. Extract the model's CLI guide to text and verify pull/pager/restore commands.
2. Subclass `ZyxelDriver` in `backend/app/devices/` and register it in
   `factory.py`.
3. Add unit tests with a fake transport (see `tests/test_drivers.py`) — CI never
   touches real hardware.
4. Update the table above.

## 8. Architecture

```
┌──────────────┐  REST (JWT)   ┌─────────────────────────────┐   SSH    ┌────────┐
│  React UI    │ ────────────▶ │  FastAPI backend            │ ───────▶ │ Zyxel  │
│  (Vite/TS)   │ ◀──────────── │  ├─ api/        routers     │ ◀────────│ devices│
└──────────────┘  JSON/text    │  ├─ services/   backup,     │          └────────┘
                              │  │              status,audit│
┌──────────────┐  cron jobs   │  ├─ devices/    transport + │   git    ┌────────┐
│ APScheduler  │ ───────────▶ │  │              3 drivers   │ ───────▶ │ config │
│ (in-process) │              │  ├─ scheduler/  job mgmt    │          │ repo   │
└──────────────┘              │  └─ db/         SQLAlchemy  │          └────────┘
┌──────────────┐  status loop │                              │  SQLite  ┌────────┐
│ Status poller│ ───────────▶ │                              │ ───────▶ │ zynk.db│
└──────────────┘              └─────────────────────────────┘          └────────┘
```

Components:

- **`app/api/`** — thin FastAPI routers (auth, devices, configs, schedules,
  status). Business logic lives in services so it is unit-testable without HTTP.
- **`app/devices/`** — `ShellTransport` (expect-style Paramiko shell wrapper:
  wide PTY to avoid line wrapping, rolling buffer, prompt-regex matching) and the
  three family drivers. SSH failures are translated into typed exceptions
  (`auth` / `unreachable` / `timeout` / `unsupported` / `failed`).
- **`app/services/backup.py`** — pull pipeline: connect → `get_config()` →
  normalize → hash → dedup against the latest snapshot → write file → git commit →
  insert DB row → audit. Revert: `apply_config()` → audit → automatic
  confirmation pull (`source=post_revert`).
- **`app/services/gitstore.py`** — thin `git` CLI wrapper; one folder per device,
  one commit per snapshot. If git fails, storage degrades gracefully to
  file-only (the `git_commit` column stays `null`).
- **`app/scheduler/jobs.py`** — APScheduler jobs reconciled from the `schedules`
  table on every change; disabled schedules are paused, not removed.
- **`app/services/status.py`** — async TCP probe (SSH port) with latency
  measurement, run in a background loop; results also pushed by the *Check
  Status* button.
- **`app/core/`** — settings (env), JWT security (HS256, PBKDF2-hashed
  passwords), Fernet credential encryption.

Snapshot lifecycle:

1. Pull is triggered (manual button, schedule, or post-revert confirmation).
2. Config is normalized and hashed; if the hash matches the latest snapshot,
   nothing is stored but the attempt is still audited.
3. Otherwise the config is written to `data/configs/<device_id>/<timestamp>-<hash8>.cfg`,
   committed to git, and a `config_snapshots` row is inserted.
4. The UI lists snapshots newest-first; diff/read/download read from the git repo
   via the DB's `rel_path`/`git_commit` columns.

## 9. Data layout & backup/restore

```
data/
├── zynk.db        SQLite: users, devices, snapshots, schedules, status, audit log
├── secret.key     JWT signing key        (auto-generated, chmod 600)
├── fernet.key     credential encryption key (auto-generated, chmod 600)
└── configs/       git repository
    └── <device_id>/
        └── <YYYYmmdd-HHMMSS>-<hash8>.cfg
```

**Backup**: stop the container (or ensure no write is in flight) and copy the
whole `data/` directory. `zynk.db` + `configs/` hold all content; the two key
files are required to decrypt stored device passwords and to keep issued JWTs
valid.

**Restore**: place the `data/` directory back and start the container.

> Losing `fernet.key` means all stored device credentials become undecryptable —
> you would have to re-enter passwords on every device (configs and history
> remain intact). Losing `secret.key` merely invalidates outstanding sessions.

**Resetting the admin password**:

- **Local development**: restart with `ZYNK_FORCE_ADMIN_RESET=true` and optionally
  `ZYNK_INITIAL_ADMIN_PASSWORD=<new-pw>`. The `admin` password is reset on that
  startup (a loud warning is printed; the reset is intended for dev only).
- **Manual**: with the container stopped, delete the `users` rows from `zynk.db`
  (e.g. `sqlite3 data/zynk.db "DELETE FROM users;"`) and restart with
  `ZYNK_INITIAL_ADMIN_PASSWORD` set — bootstrap only creates a user when the
  table is empty.

## 10. Security model

- **Device credentials** are encrypted at rest with Fernet (AES-128-CBC + HMAC);
  the key never leaves `data/fernet.key`. Passwords are never returned by any
  API response and are redacted from logs and audit entries.
- **Web login** uses PBKDF2-SHA256 (600k iterations, per-user salt) password
  hashing; sessions are HS256 JWTs with a configurable TTL.
- **Authorization**: every endpoint except `/api/health` requires a valid JWT —
  including read-only config viewing.
- **Revert safety**: the API rejects reverts without `"confirm": true`; the UI
  additionally shows an explicit danger confirmation. Every attempt (success or
  failure) is audited.
- **Audit log** is append-only through the application (no delete/update path)
  and records actor, action, target, detail and outcome.
- **Network exposure**: the app speaks plain HTTP. Do not expose it to the
  internet; bind to `127.0.0.1`/LAN or put it behind a TLS reverse proxy.
- SSH host keys are accepted on first use (TOFU) per connection; the app does
  not persist known-hosts in the alpha.

## 11. Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `[auth] Authentication failed for user …` | Wrong credentials on the device, or the account is locked out. Re-enter the password via device *Edit*. |
| `[unreachable] Cannot reach …` | Device powered off, wrong host/port, or an ACL/firewall blocks SSH from the Zynk host. Test with `ssh <user>@<host>` from the same machine. |
| `[timeout] Connection … timed out` | Slow device or network; raise `ZYNK_SSH_CONNECT_TIMEOUT_SECONDS`. |
| `[timeout] Timed out waiting for prompt` | The device's prompt doesn't match the expected pattern. ANSI escape sequences (e.g. the `ESC 7` DECSC marker XS1930 switches emit after the prompt) and `\r` are stripped automatically; if it still fails, the system prompt probably contains unexpected characters — capture the CLI prompt and extend the driver's `prompt_re`. |
| Pull says `Device returned an empty configuration` | Device answered but produced no config — often a pager or privilege issue. Verify manually with the same SSH user. |
| Revert fails with `[failed] Could not upload config to AP (FTP must be enabled…)` | AP revert needs FTP (port 21) enabled on the access point; Zynk uploads via FTP after the SSH session is established. |
| Firewall (uOS) revert fails with `all transfer methods failed` | The verified path is FTP — enable the device's FTP server (`vrf main ftp-server enabled true` + `commit` in `edit running` mode). Real-device findings: SFTP answers 'Garbage packet received' and exec-channel SCP is intercepted by the nc-cli wrapper, so both are fallbacks only; FTP must upload to `/tmp` (direct `/conf` STOR is firmware-blocked, 550) after which Zynk FTP-renames the file into `conf/`. Passive mode is required (active gets `425`). |
| Firewall (uOS) revert fails with `dry-run` in the message | The firewall itself rejected the config during validation (`cmd config-apply option dry-run`) — the device's response is included verbatim; fix the underlying config issue (the destructive apply was never sent). |
| Switch revert fails with `cannot bind TFTP listener … 69/udp` | Zynk could not bind UDP 69. In Docker publish the port (`-p 69:69/udp`); on Linux as non-root use `setcap cap_net_bind_service` or a port above 1024 via `ZYNK_TFTP_PORT` (only works if the switch firmware supports non-standard TFTP ports — usually it does not). |
| Switch revert fails with `TFTP transfer failed: no TFTP request arrived` | The switch never connected back to Zynk; the error message includes the switch's actual CLI output — read it first. The device pulls FROM Zynk: verify it can route to `ZYNK_TFTP_PUBLIC_ADDRESS` (or the auto-detected IP) on UDP 69 — in Docker bridge mode you must set `ZYNK_TFTP_PUBLIC_ADDRESS` to the host's LAN IP. Use the device **Test** button: for switches it additionally runs a TFTP path check (`copy running-config tftp …` — the switch pushes to a throwaway receiver; nothing is modified) which tells you immediately whether the UDP path works. |
| Switch revert fails with `%Invalid command "copy"` / switch refused | **Switch series CLI restriction** — the model (parsed from the config header `; Product Name = …` at pull time) determines the explanation: **XS1930/XS1935/XMG1930** ship with a restricted basic CLI and need a CLI license from Zyxel (myzyxel.com) for configuration commands; **GS1900/GS1915/XMG1915/GS1920/XGS1930/XGS1935** have no full CLI and no license can unlock it. Config *pull* works in all cases; config *restore* needs the full CLI. Series with full CLI out of the box: GS1350, RGS200, GS2220, XGS2220, XMG2230, XGS3700, XS3800, XGS4600, CX3800, CX4800. Full matrix: `backend/app/devices/switch_cli_support.csv`. The error message includes the switch's `?` listing showing which commands it does have. |
| Switch revert fails with `Switch did not come back within …s` | The warm reboot after `reload config 1` exceeded `ZYNK_SWITCH_REBOOT_TIMEOUT_SECONDS`. Check whether the restored config changed the management IP (update the device entry if so) or the device is stuck. |
| Switch revert succeeded but the confirmation pull shows an empty/old config | If the restored config changed the management IP or credentials, the post-revert pull hits the old address — update the device entry and pull again. |
| `git_commit` is `null` on new snapshots | `git` is missing or broken in the runtime; storage degrades to plain files (snapshots still work). Install git and future pulls will commit. |
| Schedule didn't fire | Cron is **UTC** — check the offset. Also confirm the schedule is enabled and targets exist (device enabled / tag matches). |
| Status shows unknown (gray dot) | The device has never been checked (new device / fresh install) — wait for the next poll (up to 5 min) or press *Check Status*. |

## 12. Limitations & roadmap

Known alpha limitations:

- Firewall (uOS) revert: upload path, tree→CLI-script conversion and dry-run
  validation are verified on real hardware; the final `cmd config-apply` step
  (which changes the running config) has not yet been executed against a live
  firewall — the first full revert should be done with console access
  available.
- Switch revert is implemented against the documented XS1930/CX4800 commands
  but not yet verified on real hardware end-to-end (and requires a full-CLI
  series — see the CLI restriction matrix) — treat the first run as an
  experiment and keep console access available.
- Status is a TCP probe of the SSH port every 5 minutes (not a full SSH login);
  each device keeps its last known state until the next check, and there is no
  WebSocket push (the UI polls every 30 s).
- Single admin user; no roles/multi-user.
- No built-in TLS; assume reverse proxy for anything beyond localhost/LAN.
- Prompt detection is regex-based and may need tuning for exotic system names.

Roadmap candidates: WebSocket status push, SNMP-free latency history graphs,
notification hooks on config-change detection.

