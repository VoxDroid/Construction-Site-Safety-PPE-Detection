# Running in production

A checklist for running PPE Guard on a site server that people rely on.

## Checklist

- [ ] A [password](../guide/security.md) is set, or the dashboard is only reachable through a VPN.
- [ ] Remote access goes through **HTTPS** (a reverse proxy, below) or a VPN.
- [ ] The server starts on boot (Docker `restart: unless-stopped`, or a service, below).
- [ ] Cameras that should always run have **Start with the server** ticked.
- [ ] A **retention period** is set in **Settings → General**, and the disk has room for it.
- [ ] The data folder is backed up.
- [ ] Alerts reach the right people (**Send a test alert**).
- [ ] The time zone is right (reports group by local day).
- [ ] You have checked detections on your own footage and tuned the confidence and delay.

## Reverse proxy with HTTPS

Run PPE Guard on `127.0.0.1:8000` and let a reverse proxy handle HTTPS. The live video
(`/api/cameras/*/stream`) is a long-lived response, and the live feed (`/ws`) is a WebSocket, so
the proxy must not buffer the first or block the second.

=== "Caddy"

    ```caddyfile
    ppe.example.com {
        reverse_proxy 127.0.0.1:8000 {
            flush_interval -1
        }
    }
    ```

    Caddy gets and renews the certificate itself.

=== "nginx"

    ```nginx
    server {
        listen 443 ssl;
        server_name ppe.example.com;
        ssl_certificate     /etc/letsencrypt/live/ppe.example.com/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/ppe.example.com/privkey.pem;
        client_max_body_size 500m;            # Analyze uploads

        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            proxy_set_header Upgrade $http_upgrade;     # WebSocket
            proxy_set_header Connection "upgrade";
            proxy_buffering off;                        # MJPEG streams
            proxy_read_timeout 1h;
        }
    }
    ```

When the proxy runs on the same machine, PPE Guard trusts its `X-Forwarded-Proto` header and marks
the session cookie `Secure`. If the proxy is on another machine, set
`FORWARDED_ALLOW_IPS=<proxy ip>` in PPE Guard's environment.

Set **Settings → Alerts → Dashboard address** to `https://ppe.example.com` so alerts link to it.

## Start on boot

=== "Linux (systemd)"

    ```ini title="/etc/systemd/system/ppe-guard.service"
    [Unit]
    Description=PPE Guard
    After=network-online.target
    Wants=network-online.target

    [Service]
    User=ppe
    WorkingDirectory=/opt/ppe-guard
    Environment=PPE_GUARD_DATA_DIR=/opt/ppe-guard/data
    Environment=PPE_GUARD_MODELS_DIR=/opt/ppe-guard/models
    ExecStart=/opt/ppe-guard/.venv/bin/ppe-guard serve --host 127.0.0.1 --port 8000
    Restart=on-failure

    [Install]
    WantedBy=multi-user.target
    ```

    ```bash
    sudo systemctl enable --now ppe-guard
    journalctl -u ppe-guard -f
    ```

=== "Windows"

    Create a scheduled task that runs at startup (Task Scheduler → *Create Task* → trigger *At
    startup*, *Run whether user is logged on or not*) with the action:

    ```text
    C:\ppe-guard\.venv\Scripts\ppe-guard.exe serve --data-dir C:\ppe-guard\data --models-dir C:\ppe-guard\models
    ```

    Or wrap the same command as a service with a tool such as NSSM.

=== "Docker"

    `docker compose up -d` with `restart: unless-stopped` (the default in the provided compose
    file) restarts it after reboots.

## Storage and backups

Everything lives in the data folder:

| Path | Content |
| --- | --- |
| `settings.json` | Settings, cameras and zones |
| `auth.json` | Password hash, session key, API token hash |
| `ppe_guard.db` (+ `-wal`, `-shm`) | Violations and worker-time (SQLite) |
| `snapshots/`, `clips/` | Evidence |
| `analysis/` | Analyze results |

To back it up, stop the server (or copy the database with `sqlite3 ppe_guard.db ".backup backup.db"`)
and copy the folder. Restoring is copying it back.

Rough sizes: a violation with five snapshots and a 20-second clip takes a few megabytes. A busy
camera with 50 violations a day needs on the order of 100-200 MB per day; set
**Keep violations for (days)** accordingly.

## Monitoring

- `GET /api/health` returns `{"status": "ok"}` and needs no login. Point your uptime monitor at it.
- `GET /api/stats` (with a token) returns open violations, cameras running and more, for a
  dashboard or alerting system.
- Camera errors and alert failures are printed to the server log.

## Updating

```bash
pip install --upgrade "git+https://github.com/VoxDroid/ppe-guard.git"
```

then restart the service. Database changes are applied automatically on start, and settings
files from older versions keep working.
