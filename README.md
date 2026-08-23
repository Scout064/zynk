# Zynk — Zyxel Network Configuration Backup & Management

Self-hosted tool that backs up, versions, diffs and (where supported) restores the
configuration of Zyxel network devices over SSH.

**Alpha status** — core backup workflows are implemented and tested against mocked
SSH sessions; verify against your real hardware before relying on it.

## Features (alpha)

- Device inventory (switches, firewalls, access points) with encrypted credential storage
- Manual and scheduled (cron) config pulls via SSH
- Snapshot history with content-hash dedup (unchanged configs are not re-stored)
- Config storage in a **git repository** (folder per device) + SQLite index
- Unified diff between any two snapshots, snapshot viewer and download
- Zip export of a device's full history
- Online/offline status polling (TCP probe of the SSH port) every 5 minutes;
  each device keeps its last known state until the next check
- JWT-authenticated REST API + modern dark-themed web UI
- Immutable audit log of every login, pull, export, revert and inventory change

## Supported devices (per the CLI guides in `CLI-GUIDE/`)

| Family | Models (verified against guide) | Config pull | Revert |
|---|---|---|---|
| Switch | All ZyNOS and FaOS based Switches | `show running-config` | yes — TFTP + `copy tftp config` + `reload config` (device reboots) |
| Firewall (uOS) | All uOS based | `show config running \| no-pager` | not in alpha (needs file staged on device) |
| Firewall (ZLD) | All ZLD based (ATP & USG ZyWALL) | `show running-config` | yes — FTP upload + `apply /conf/<file> ignore-error rollback` + `write` |
| Access Point | ZyNOS based | `show running-config` | yes — FTP upload + `apply running-config ... ignore error rollback` + `write` |

> ⚠ **Switch revert reboots the device:** the snapshot is staged over TFTP
> (Zynk serves it on UDP port 69 — the switch must be able to reach Zynk) and
> applied with `reload config 1`, a warm reboot. If the restored config changes
> the management IP, update the device entry afterwards. In Docker, publish
> `69/udp` and set `ZYNK_TFTP_PUBLIC_ADDRESS` to the host's LAN IP.
>
> ⚠ **XS1930 license restriction:** the XS1930 series ships with a restricted
> *basic* CLI (config pull works, but `copy tftp config` is unavailable).
> Switch **revert requires the Access L3 license** from Zyxel (myzyxel.com) to
> unlock full CLI configuration. Other switch series (e.g. GS1350, CX4800) have
> the full CLI without a license.

> ⚠ **End of Life:** ZLD-based devices (USG & ATP series) are in End-of-Life
> state at Zyxel — no further firmware updates or support. They remain
> supported by Zynk for existing installations, but plan migration to a
> current platform (e.g. USG FLEX H / uOS).

## Quick start (Docker)

```bash
cd docker
docker compose up -d --build
```

The app runs on `http://localhost:8000`. On first start an `admin` user is created:

- If `ZYNK_INITIAL_ADMIN_PASSWORD` is set, that password is used.
- Otherwise a random password is generated and printed once to the container logs
  (`docker logs zynk`).

All data (SQLite DB, git config repo, encryption keys) lives in the mounted `./data`
volume. **Back up `data/secret.key` and `data/fernet.key` — without them stored device
passwords cannot be decrypted.**

## Local development

```bash
# backend (install must run from backend/ — that's where pyproject.toml lives)
python3 -m venv .venv
cd backend
../.venv/bin/pip install -e ".[dev]"
../.venv/bin/python -m uvicorn app.main:app --reload

# frontend (in another terminal)
cd frontend && npm install && npm run dev   # http://localhost:5173, proxies /api
```

### Tests / lint

```bash
cd backend
../.venv/bin/python -m pytest              # unit + API tests (SSH fully mocked)
../.venv/bin/ruff check .
../.venv/bin/black --check .
```

## Security notes

- Device credentials are encrypted at rest (Fernet) and never returned by the API or
  written to logs.
- All endpoints require a valid JWT; there is no unauthenticated access, including
  read-only config viewing.
- Revert is a destructive action: the UI requires an explicit confirmation and every
  attempt is recorded in the audit log.
- The app holds firewall configs and device credentials — do **not** expose it to the
  internet without TLS. Bind to localhost/LAN or front it with a reverse proxy.

## Configuration (environment variables, prefix `ZYNK_`)

| Variable | Default | Description |
|---|---|---|
| `ZYNK_DATA_DIR` | `./data` | Runtime data directory |
| `ZYNK_INITIAL_ADMIN_PASSWORD` | *(random, printed once)* | First-run admin password |
| `ZYNK_FORCE_ADMIN_RESET` | `false` | **Dev only:** reset the `admin` password to `ZYNK_INITIAL_ADMIN_PASSWORD` (or a newly generated one) on every startup — escape hatch for a lost local password. Never enable in production. |
| `ZYNK_STATUS_POLL_INTERVAL_SECONDS` | `300` | Device status poll interval (5 min default) |
| `ZYNK_SSH_CONNECT_TIMEOUT_SECONDS` | `15` | SSH connect timeout |
| `ZYNK_SSH_COMMAND_TIMEOUT_SECONDS` | `120` | SSH command timeout |
| `ZYNK_ACCESS_TOKEN_TTL_MINUTES` | `720` | JWT lifetime |
| `ZYNK_TFTP_PUBLIC_ADDRESS` | *(auto-detect)* | IP switches use to reach Zynk for config restores (set explicitly in Docker) |
| `ZYNK_TFTP_PORT` | `69` | TFTP listen port for switch restores (UDP) |
| `ZYNK_SWITCH_REBOOT_TIMEOUT_SECONDS` | `300` | Max time to wait for a switch to come back after `reload config` |

## Architecture

```
backend/   FastAPI app (api/, core/, db/, devices/, scheduler/, services/)
frontend/  React + TypeScript + Vite + Tailwind
docker/    Dockerfile + docker-compose.yml
data/      runtime: sqlite db, per-device git repo, keys  (gitignored)
```

See [docs/DOCUMENTATION.md](docs/DOCUMENTATION.md) for the full documentation
(installation, UI guide, REST API reference, device driver/CLI details,
architecture, backup/restore runbook and troubleshooting).

> **Note for developers:** this project is developed with AI-agent assistance.
> The agent instructions (`AGENTS.md`) and the vendor CLI reference PDFs
> (`CLI-GUIDE/`) are intentionally not committed — see `.gitignore`.
