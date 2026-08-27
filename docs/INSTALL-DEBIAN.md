# Zynk Installation Guide — Debian-based Linux

Step-by-step install guide for Debian 12 (Bookworm), Debian 13 (Trixie),
Ubuntu 22.04/24.04 LTS and derivatives. Two installation paths:

- **[Option 1 — Manual install](#option-1--manual-install)**: Python venv +
  systemd service. Full control, no Docker required.
- **[Option 2 — Docker install](#option-2--docker-install)**: single
  container via docker compose. Fastest path.

If you only want to try Zynk quickly, use Docker. If you prefer distro-native
administration (systemd, unattended-upgrades on the OS level), use the manual
install.

---

## Prerequisites (both options)

A host on the same network as your Zyxel devices that can reach them on
TCP 22 (SSH). Additionally:

| Requirement | Why |
|---|---|
| TCP 22 outbound to devices | Config pulls, test connections |
| UDP 69 inbound (only for switch restore) | The switch downloads the snapshot from Zynk via TFTP |
| TCP 21 inbound (only for AP / ZLD firewall restore) | The device fetches the pushed config via FTP |
| ~200 MB disk + space for configs | App + git-backed config history |

Pick (and note) the data directory. This guide uses `/opt/zynk` for the app
and `/var/lib/zynk` for data. Both paths belong to the `zynk` service user.

---

## Option 1 — Manual Install

### 1.1 Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git curl
```

Debian 12 / Ubuntu 22.04 ship Python 3.11; Zynk requires **3.12+**. Check:

```bash
python3 --version
```

If it prints 3.11 or older, install Python 3.12 from the deadsnakes PPA
(Ubuntu) or use the Docker install instead (Debian does not carry 3.12 in
Bookworm; Trixie does):

```bash
# Ubuntu 22.04/24.04 only:
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.12 python3.12-venv
python3.12 --version   # must be 3.12.x
```

### 1.2 Create the service user and directories

```bash
sudo useradd --system --home /opt/zynk --shell /usr/sbin/nologin zynk
sudo mkdir -p /opt/zynk /var/lib/zynk
```

### 1.3 Get the code

Either a release tarball (no git history needed):

```bash
curl -LO https://github.com/Scout064/zynk/archive/refs/tags/v0.5.0.tar.gz
sudo tar xzf v0.5.0.tar.gz -C /opt/zynk --strip-components=1
```

…or clone the repository (main branch):

```bash
sudo git clone https://github.com/Scout064/zynk.git /opt/zynk
```

### 1.4 Build the frontend

Requires Node.js 20+ (only for the build; it is not needed at runtime):

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
cd /opt/zynk/frontend
sudo npm ci
sudo npm run build     # creates frontend/dist/
```

### 1.5 Create the Python environment and install the backend

```bash
cd /opt/zynk/backend
sudo python3.12 -m venv /opt/zynk/.venv     # or python3 -m venv if 3.12 is default
sudo /opt/zynk/.venv/bin/pip install --upgrade pip
sudo /opt/zynk/.venv/bin/pip install .
```

### 1.6 Set permissions

```bash
sudo chown -R zynk:zynk /opt/zynk /var/lib/zynk
```

### 1.7 Create the systemd service

```bash
sudo tee /etc/systemd/system/zynk.service > /dev/null <<'EOF'
[Unit]
Description=Zynk — Zyxel config backup
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=zynk
Group=zynk
WorkingDirectory=/opt/zynk/backend
Environment=ZYNK_DATA_DIR=/var/lib/zynk
Environment=ZYNK_HOST=127.0.0.1
# Choose the initial admin password (created on first start only):
Environment=ZYNK_INITIAL_ADMIN_PASSWORD=change-me-now
# Device status poll interval (seconds):
Environment=ZYNK_STATUS_POLL_INTERVAL_SECONDS=300
ExecStart=/opt/zynk/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/var/lib/zynk
ProtectHome=true

[Install]
WantedBy=multi-user.target
EOF
```

> **Port 69 note (switch restore):** the service above runs unprivileged, so
> it cannot bind UDP 69. Either skip switch restores, or add:
>
> ```ini
> AmbientCapabilities=CAP_NET_BIND_SERVICE
> CapabilityBoundingSet=CAP_NET_BIND_SERVICE
> ```
>
> to the `[Service]` section, and set `ZYNK_TFTP_PUBLIC_ADDRESS` to the
> host's LAN IP.

Apply the capability variant if needed, then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now zynk
```

### 1.8 Verify

```bash
systemctl status zynk --no-pager
curl http://127.0.0.1:8000/api/health
# {"status":"ok","version":"0.5.0"}
sudo journalctl -u zynk -n 50 --no-pager   # first-run admin password if unset
```

The web UI is now on `http://127.0.0.1:8000` (localhost only). See
[Post-install](#post-install-steps) for LAN access and the reverse proxy.

---

## Option 2 — Docker Install

### 2.1 Install Docker Engine + compose plugin

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker run --rm hello-world   # sanity check
```

(On Ubuntu use the same commands with `linux/ubuntu` in the repository URL.)

### 2.2 Get the code

The container is **built from the repository** (the compose file builds with
the repo root as its context), so clone it:

```bash
sudo git clone https://github.com/Scout064/zynk.git /opt/zynk
cd /opt/zynk
```

### 2.3 Configure

Edit `docker/docker-compose.yml`:

1. **`ZYNK_INITIAL_ADMIN_PASSWORD`** — set your own (created on first start
   only; if left at `change-me-now` a random password is generated and
   printed once to `docker logs`).
2. **`ZYNK_TFTP_PUBLIC_ADDRESS`** — uncomment and set to the **host's LAN
   IP** (e.g. `192.168.1.10`). Required for switch restores: the switch must
   reach the container's mapped UDP 69 via the host IP.
3. Data lives in `data/` inside the checkout (the compose volume `../data`
   resolves relative to the compose file at `docker/`, i.e. `/opt/zynk/data`
   — the directory is gitignored). Adjust to your liking (e.g.
   `/var/lib/zynk:/data`).

### 2.4 Build and start

```bash
cd /opt/zynk
sudo docker compose -f docker/docker-compose.yml up -d --build
```

### 2.5 Verify

```bash
sudo docker compose -f docker/docker-compose.yml ps
curl http://127.0.0.1:8000/api/health
sudo docker compose -f docker/docker-compose.yml logs zynk | grep -A2 "admin"   # first-run password if generated
```

Also verify the web UI itself (not just the API — the SPA is served by the
same container):

```bash
curl -sI http://127.0.0.1:8000/ | head -1   # must be 200, not 404
```

Web UI: `http://<host-ip>:8000`.

---

## Post-install steps

### Firewall (UFW example)

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8000 proto tcp   # web UI (LAN only!)
sudo ufw allow from 192.168.1.0/24 to any port 69 proto udp     # switch restore (TFTP)
sudo ufw allow from 192.168.1.0/24 to any port 21 proto tcp     # AP/ZLD restore (FTP)
sudo ufw enable
```

Restrict these to your management subnet — Zynk holds device credentials
and firewall configs. Do not expose port 8000 to the internet without TLS
(see below).

### First login

1. Browse to `http://<host>:8000`, log in as `admin` with the configured or
   generated password (Docker: `docker logs`, manual: `journalctl -u zynk`).
2. Change the password immediately: see DOCUMENTATION.md §6.1
   (`POST /api/auth/change-password`).
3. *Devices → + Add Device* — enter host, family, SSH credentials.
4. Press **Test** — verifies SSH login (switches: also the TFTP path).
5. **Pull Config Now** — first snapshot.
6. Optional: *Schedules → + Add Schedule* (cron, UTC).

### LAN / remote access with TLS (recommended)

The manual install binds to `127.0.0.1` by default; Docker binds
`0.0.0.0:8000`. For anything beyond localhost/LAN, put a reverse proxy with
TLS in front:

```nginx
server {
    listen 443 ssl;
    server_name zynk.example.com;
    ssl_certificate     /etc/ssl/certs/zynk.pem;
    ssl_certificate_key /etc/ssl/private/zynk.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Backups

Everything persistent lives in the data directory (`/var/lib/zynk` manual,
or the compose volume). Stop the service (or ensure no write is in flight)
and copy the whole directory. **`secret.key` and `fernet.key` are essential**
— without `fernet.key` stored device passwords cannot be decrypted.

### Updating

Manual install:

```bash
cd /opt/zynk
sudo -u zynk git fetch --tags
sudo -u zynk git checkout v0.5.0          # or the new version
cd frontend && sudo -u zynk npm ci && sudo -u zynk npm run build && cd ..
cd backend && sudo -u zynk /opt/zynk/.venv/bin/pip install . && cd ..
sudo systemctl restart zynk
```

Docker:

```bash
cd /opt/zynk
sudo git pull
sudo docker compose -f docker/docker-compose.yml up -d --build
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `unit/zynk.service: Failed` — venv not found | Check `ExecStart` path matches `/opt/zynk/.venv/bin/uvicorn` and `WorkingDirectory=/opt/zynk/backend`. |
| Python 3.12 not available (Debian 12) | Use the Docker install, or build Python 3.12 from source. |
| Web UI unreachable from LAN | Manual install binds `127.0.0.1` — change `ZYNK_HOST=0.0.0.0` (and protect it with a firewall/proxy). |
| Docker: `GET /` returns 404 while `/api/health` works | The static-frontend mount was skipped — the app couldn't find `frontend/dist` (path layout differs between dev and image). Fixed since v0.5.0 with a layout-aware lookup; verify with `curl -sI http://localhost:8000/` expecting 200 after `docker compose up -d --build`. |
| Switch restore: `cannot bind TFTP listener … 69/udp` | Manual: add the `AmbientCapabilities=CAP_NET_BIND_SERVICE` lines (§1.7). Docker: keep the `69:69/udp` port mapping. |
| Switch restore: `no TFTP request arrived` | Set `ZYNK_TFTP_PUBLIC_ADDRESS` to the host LAN IP (Docker bridge networking hides the host). See DOCUMENTATION.md §11. |
| Forgot the admin password | Restart once with `ZYNK_FORCE_ADMIN_RESET=true` + `ZYNK_INITIAL_ADMIN_PASSWORD=<new>` (dev escape hatch; disable afterwards). |

More: [DOCUMENTATION.md §11 (Troubleshooting)](DOCUMENTATION.md#11-troubleshooting).
