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
- Online/offline status polling (TCP probe of the SSH port) on a configurable interval
- JWT-authenticated REST API + modern dark-themed web UI
- Immutable audit log of every login, pull, export, revert and inventory change

## Supported devices (per the CLI guides in `CLI-GUIDE/`)

| Family | Models (verified against guide) | Config pull | Revert |
|---|---|---|---|
| Switch | All ZyNOS and FaOS based Switches | `show running-config` | not in alpha (needs TFTP + reload) |
| Firewall | All uOS based | `show config running \| no-pager` | not in alpha (needs file staged on device) |
| Access Point | ZyNOS based | `show running-config` | yes — FTP upload + `apply running-config ... ignore error rollback` + `write` |

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
| `ZYNK_STATUS_POLL_INTERVAL_SECONDS` | `60` | Device status poll interval |
| `ZYNK_SSH_CONNECT_TIMEOUT_SECONDS` | `15` | SSH connect timeout |
| `ZYNK_SSH_COMMAND_TIMEOUT_SECONDS` | `120` | SSH command timeout |
| `ZYNK_ACCESS_TOKEN_TTL_MINUTES` | `720` | JWT lifetime |

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
