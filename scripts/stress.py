"""Load test: many cameras, a large database, API clients, stream viewers and WebSocket clients.

Starts `ppe-guard serve` in a subprocess with N looping video cameras (real model), seeds the
database, then measures for --duration seconds:

* per-camera processing fps,
* API latency (p50/p95/max) and errors under concurrent requests,
* MJPEG viewers and WebSocket clients receiving data,
* server memory and CPU, and GPU memory when nvidia-smi is available.

    python scripts/stress.py --videos samples/*.mp4 --cameras 8 --duration 180

Prints a JSON report and writes it next to the data folder.
"""

from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import random
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import psutil


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def seed(data_dir: Path, n: int, cameras: list[str]) -> None:
    from ppe_guard.storage import Storage

    s = Storage(data_dir)
    now = time.time()
    rows = []
    for i in range(n):
        start = now - random.uniform(0, 90 * 86400)
        dur = random.expovariate(1 / 20)
        zone = random.random() < 0.1
        cam = random.choice(cameras)
        missing = [] if zone else random.choice([["helmet"], ["vest"], ["helmet", "vest"], ["mask"]])
        rows.append((f"seed{i:07d}", cam, cam.title(), f"t{i % 500}", json.dumps(missing), start,
                     start + dur, start + dur, int(random.random() < 0.6), None,
                     "zone" if zone else "ppe", "pit" if zone else None, "Pit" if zone else None, None))
    s._db.executemany(
        "INSERT INTO violations (id, camera_id, camera_name, worker_key, missing, started_at, last_seen,"
        " ended_at, acknowledged, note, kind, zone_id, zone_name, clip) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows)
    hours = 90 * 24
    s._db.executemany(
        "INSERT OR REPLACE INTO activity VALUES (?,?,?,?,?)",
        [(c, (now - h * 3600) - (now - h * 3600) % 3600, random.uniform(0, 7200), random.uniform(0, 600),
          random.randint(0, 6)) for c in cameras for h in range(hours)])
    s._db.commit()
    s.close()


def gpu_memory_mb() -> float | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        return float(out.strip().splitlines()[0])
    except Exception:
        return None


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, round(p / 100 * (len(values) - 1)))]


API_PATHS = [
    "/api/stats", "/api/cameras", "/api/violations?limit=25", "/api/violations?limit=25&offset=5000",
    "/api/violations?item=vest&sort=duration&order=desc&limit=50", "/api/analytics?days=7",
    "/api/analytics?days=90", "/api/violations?kind=zone&acknowledged=false&limit=50",
    "/api/report?days=7", "/api/settings",
]


async def api_load(base: str, stop: float, concurrency: int, out: dict) -> None:
    lat: dict[str, list[float]] = {p.split("?")[0] + ("?" + p.split("?")[1][:20] if "?" in p else ""): []
                                   for p in API_PATHS}
    errors: list[str] = []
    keys = list(lat)

    async def worker(client: httpx.AsyncClient) -> None:
        i = random.randrange(len(API_PATHS))
        while time.time() < stop:
            path = API_PATHS[i % len(API_PATHS)]
            i += 1
            t0 = time.perf_counter()
            try:
                r = await client.get(base + path, timeout=30)
                if r.status_code != 200:
                    errors.append(f"{r.status_code} {path}")
            except Exception as e:
                errors.append(f"{type(e).__name__} {path}")
            lat[keys[API_PATHS.index(path)]].append((time.perf_counter() - t0) * 1000)

    async with httpx.AsyncClient() as client:
        await asyncio.gather(*(worker(client) for _ in range(concurrency)))
    allv = [v for vs in lat.values() for v in vs]
    out["api"] = {
        "requests": len(allv),
        "errors": len(errors),
        "error_samples": errors[:5],
        "p50_ms": round(pct(allv, 50), 1),
        "p95_ms": round(pct(allv, 95), 1),
        "max_ms": round(max(allv) if allv else 0, 1),
        "per_endpoint_p95_ms": {k: round(pct(v, 95), 1) for k, v in lat.items()},
    }


async def ws_clients(url: str, stop: float, n: int, out: dict) -> None:
    import websockets

    counts = [0] * n

    async def one(i: int) -> None:
        try:
            async with websockets.connect(url, max_size=None) as ws:
                while time.time() < stop:
                    try:
                        await asyncio.wait_for(ws.recv(), timeout=2)
                        counts[i] += 1
                    except asyncio.TimeoutError:
                        pass
        except Exception as e:
            out.setdefault("ws_errors", []).append(f"{type(e).__name__}: {e}")

    await asyncio.gather(*(one(i) for i in range(n)))
    out["websocket"] = {"clients": n, "messages_min": min(counts), "messages_total": sum(counts)}


def mjpeg_viewer(url: str, stop: float, result: list, i: int) -> None:
    frames = 0
    try:
        with httpx.stream("GET", url, timeout=30) as r:
            for chunk in r.iter_bytes():
                frames += chunk.count(b"--frame")
                if time.time() >= stop:
                    break
    except Exception as e:
        result[i] = (frames, f"{type(e).__name__}")
        return
    result[i] = (frames, None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", nargs="+", required=True, help="video files to use as cameras (globs ok)")
    ap.add_argument("--cameras", type=int, default=8)
    ap.add_argument("--duration", type=float, default=180)
    ap.add_argument("--warmup", type=float, default=30)
    ap.add_argument("--seed-violations", type=int, default=100_000)
    ap.add_argument("--api-concurrency", type=int, default=16)
    ap.add_argument("--viewers", type=int, default=8)
    ap.add_argument("--ws-clients", type=int, default=20)
    ap.add_argument("--max-fps", type=float, default=15)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--data-dir", help="default: a new temporary folder")
    ap.add_argument("--keep", action="store_true", help="keep the data folder")
    a = ap.parse_args()

    videos = [str(Path(p).resolve()) for g in a.videos for p in sorted(glob.glob(g))]
    if not videos:
        sys.exit("no videos found")
    data = Path(a.data_dir or tempfile.mkdtemp(prefix="ppe-stress-"))
    data.mkdir(parents=True, exist_ok=True)
    cams = [f"cam{i + 1}" for i in range(a.cameras)]
    settings = {
        "general": {"site_name": "Stress test"},
        "detection": {"max_fps": a.max_fps, "imgsz": a.imgsz},
        "cameras": [{"id": c, "name": f"Camera {i + 1}", "source": videos[i % len(videos)], "autostart": True,
                     "loop": True} for i, c in enumerate(cams)],
    }
    (data / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    print(f"seeding {a.seed_violations} violations in {data}", flush=True)
    seed(data, a.seed_violations, cams)

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    log = open(data / "server.log", "w", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen([sys.executable, "-m", "ppe_guard", "serve", "--port", str(port), "--data-dir", str(data)],
                            stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUNBUFFERED": "1"})
    report: dict = {"cameras": a.cameras, "videos": [Path(v).name for v in videos], "duration_s": a.duration,
                    "seeded_violations": a.seed_violations, "max_fps": a.max_fps, "imgsz": a.imgsz}
    try:
        for _ in range(120):
            try:
                if httpx.get(base + "/api/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("server did not start")
        print(f"server up on {base}; warming up {a.warmup}s", flush=True)
        time.sleep(a.warmup)
        p = psutil.Process(proc.pid)
        # A Windows venv's python.exe is a launcher; the server is its child process.
        kids = p.children(recursive=True)
        if kids:
            p = max(kids, key=lambda c: c.memory_info().rss)
        p.cpu_percent(None)
        rss_start = p.memory_info().rss / 2**20
        gpu_start = gpu_memory_mb()

        stop = time.time() + a.duration
        viewer_results: list = [None] * a.viewers
        viewers = [threading.Thread(target=mjpeg_viewer, daemon=True,
                                    args=(f"{base}/api/cameras/{cams[i % len(cams)]}/stream", stop, viewer_results, i))
                   for i in range(a.viewers)]
        for t in viewers:
            t.start()

        samples: list[dict] = []

        def sampler() -> None:
            while time.time() < stop:
                try:
                    cs = httpx.get(base + "/api/cameras", timeout=10).json()
                    samples.append({"t": time.time(), "fps": {c["id"]: c["fps"] for c in cs},
                                    "status": {c["id"]: c["status"] for c in cs},
                                    "rss_mb": p.memory_info().rss / 2**20, "cpu": p.cpu_percent(None)})
                except Exception as e:
                    samples.append({"t": time.time(), "error": str(e)})
                time.sleep(5)

        st = threading.Thread(target=sampler, daemon=True)
        st.start()

        async def run_async() -> dict:
            out: dict = {}
            await asyncio.gather(api_load(base, stop, a.api_concurrency, out),
                                 ws_clients(f"ws://127.0.0.1:{port}/ws", stop, a.ws_clients, out))
            return out

        report.update(asyncio.run(run_async()))
        st.join(10)
        for t in viewers:
            t.join(10)

        ok = [s for s in samples if "fps" in s]
        fps_by_cam = {c: [s["fps"][c] for s in ok if s["status"].get(c) == "running"] for c in cams}
        report["camera_fps"] = {
            "mean_per_camera": round(statistics.mean([statistics.mean(v) for v in fps_by_cam.values() if v]), 2),
            "min_per_camera": round(min(min(v) for v in fps_by_cam.values() if v), 2),
            "total": round(sum(statistics.mean(v) for v in fps_by_cam.values() if v), 1),
            "not_running_samples": sum(1 for s in ok for c in cams if s["status"].get(c) != "running"),
        }
        report["mjpeg_viewers"] = {"viewers": a.viewers,
                                   "frames_min": min(r[0] for r in viewer_results if r),
                                   "errors": [r[1] for r in viewer_results if r and r[1]]}
        report["server"] = {
            "rss_start_mb": round(rss_start, 0),
            "rss_end_mb": round(p.memory_info().rss / 2**20, 0),
            "rss_max_mb": round(max(s["rss_mb"] for s in ok), 0) if ok else None,
            "cpu_mean_pct": round(statistics.mean(s["cpu"] for s in ok[1:]), 0) if len(ok) > 1 else None,
            "logical_cpus": psutil.cpu_count(),
            "threads_end": p.num_threads(),
        }
        g = gpu_memory_mb()
        report["gpu_memory_mb"] = {"start": gpu_start, "end": g}
        report["violations_recorded_during_test"] = httpx.get(base + "/api/stats", timeout=30).json()["total"] - a.seed_violations
        report["server_still_healthy"] = httpx.get(base + "/api/health", timeout=10).status_code == 200
    finally:
        proc.terminate()
        try:
            proc.wait(30)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
    errors_in_log = [ln.strip() for ln in (data / "server.log").read_text(encoding="utf-8", errors="replace").splitlines()
                     if "Traceback" in ln or "Exception" in ln]
    report["server_log_exceptions"] = errors_in_log[:10]
    out = data / "stress-report.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not a.keep and not a.data_dir:
        shutil.rmtree(data, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
