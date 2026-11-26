# Docker

The repository includes a `Dockerfile` and a `docker-compose.yml`. The image runs the dashboard
on port 8000 as an unprivileged user and keeps everything you want to keep in two volumes.

| Volume | Holds |
| --- | --- |
| `/data` | `settings.json`, the database, snapshots, clips, Analyze results and `auth.json` |
| `/models` | Model weights (downloaded on first use if missing) |

## With Docker Compose

```bash
git clone https://github.com/VoxDroid/ppe-guard.git
cd ppe-guard
docker compose up -d --build
```

Open <http://localhost:8000>. `./data` and `./models` next to `docker-compose.yml` hold the
volumes, so they survive upgrades.

Edit `docker-compose.yml` to:

- **Set a password**: uncomment `PPE_GUARD_PASSWORD`. Do this before exposing the port beyond
  your own machine.
- **Set the time zone** with `TZ` (for example `Europe/London` or `Asia/Manila`), so analytics
  and reports use your local days.
- **Use video files as cameras**: uncomment the `./videos:/videos:ro` volume and use sources such
  as `/videos/site.mp4`.
- **Use a USB webcam** (Linux): uncomment the `devices` section and use source `0`.

## NVIDIA GPU

The default image uses CPU-only PyTorch, which is much smaller. For a GPU, install the
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html),
then build with the CUDA wheels and uncomment the `deploy` section in `docker-compose.yml`:

```bash
docker compose build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cu128
docker compose up -d
```

**Settings → System** shows the GPU's name when it is in use.

## Plain `docker run`

```bash
docker build -t ppe-guard .
docker run -d --name ppe-guard -p 8000:8000 \
  -v "$PWD/data:/data" -v "$PWD/models:/models" \
  -e PPE_GUARD_PASSWORD="a long passphrase" -e TZ=Europe/London \
  --restart unless-stopped ppe-guard
```

## Notes

- The container starts as root only long enough to give the `ppe` user (UID 1000) ownership of
  `/data` and `/models`, then runs the server as that user.
- IP cameras must be reachable from the container. With the default bridge network that works
  for cameras on your LAN; use `--network host` on Linux if your cameras need multicast.
- To update: `git pull && docker compose up -d --build`. The database is migrated automatically.
- The health check calls `/api/health` every 30 seconds.
