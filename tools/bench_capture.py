"""Benchmark capture FPS for MOONLIGHT: mss vs dxcam (DXGI desktop duplication).

- DPI-aware (physical pixels) on the primary monitor.
- Client-window-sized regions (task T02 asks for ~1280x960, the classic 4:3
  Tibia window). We benchmark 1280x960 (primary), plus 1280x720 and 1024x768
  for reference.
- Tight-loop capture for DURATION seconds; reports max loop FPS + per-frame
  latency (min/avg/p50/p99). For 20 Hz feasibility the key numbers are:
    * avg_fps  (want >> 20)
    * p99_ms   (want < 50 ms = 1/20 s)
- Samples nvidia-smi (GPU util + VRAM) on a background thread with timestamps,
  and psutil for process/system/LLM CPU, mapping them to each phase window.

Writes JSON results to tools/capture_bench_results.json.

Usage:
    python tools/bench_capture.py [--duration 5.0]
"""
import argparse
import contextlib
import ctypes
import json
import statistics
import threading
import time
from pathlib import Path

# DPI awareness so geometry is in physical pixels
try:
    ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)  # PER_MONITOR_AWARE_V2
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

import psutil

PRIMARY = {"left": 0, "top": 0, "width": 2560, "height": 1440}
# (left, top, right, bottom) in physical pixels on the 2560x1440 primary
REGIONS = {
    "1280x960": (640, 240, 1920, 1200),   # <- primary: 4:3 Tibia client window
    "1280x720": (640, 360, 1920, 1080),
    "1024x768": (768, 336, 1792, 1104),
}
WARMUP = 0.75  # seconds


def region_for(name: str) -> tuple:
    return REGIONS[name]


def latency_stats(deltas: list[float]) -> dict:
    if not deltas:
        return {}
    d = sorted(deltas)
    n = len(d)

    def pct(p: float) -> float:
        return d[min(n - 1, int(n * p))]

    return {
        "n": n,
        "min_ms": round(d[0] * 1000, 3),
        "avg_ms": round(sum(d) / n * 1000, 3),
        "p50_ms": round(pct(0.50) * 1000, 3),
        "p99_ms": round(pct(0.99) * 1000, 3),
    }


class GpuSampler(threading.Thread):
    """Samples nvidia-smi in the background with monotonic timestamps."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.samples: list[dict] = []
        self._stop = threading.Event()

    def run(self) -> None:
        import subprocess
        while not self._stop.is_set():
            try:
                r = subprocess.run(
                    ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5)
                parts = r.stdout.strip().split(",")
                self.samples.append({
                    "ts": time.monotonic(),
                    "gpu_util": int(parts[0].strip()),
                    "mem_used_mib": int(parts[1].strip()),
                })
            except Exception:
                pass
            self._stop.wait(0.4)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=3)

    def window(self, t0: float, t1: float) -> dict:
        """Average GPU util + max VRAM over [t0, t1] (monotonic seconds)."""
        rows = [s for s in self.samples if t0 - 0.6 <= s["ts"] <= t1 + 0.6]
        if not rows:
            return {}
        return {
            "gpu_util_avg": round(statistics.mean(r["gpu_util"] for r in rows), 1),
            "gpu_util_max": max(r["gpu_util"] for r in rows),
            "vram_used_mib_avg": round(statistics.mean(r["mem_used_mib"] for r in rows), 0),
            "vram_used_mib_max": max(r["mem_used_mib"] for r in rows),
        }


def bench_mss(region_name: str, duration: float) -> dict:
    import mss

    x, y, r, b = region_for(region_name)
    region = {"left": x, "top": y, "width": r - x, "height": b - y}
    deltas: list[float] = []
    frames = 0

    with mss.MSS() as sct:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < WARMUP:
            sct.grab(region)
        t_start = time.perf_counter()
        end = t_start + duration
        while time.perf_counter() < end:
            t_grab_start = time.perf_counter()
            shot = sct.grab(region)
            _ = shot.raw  # materialize raw bytes
            now = time.perf_counter()
            deltas.append(now - t_grab_start)
            frames += 1
    wall = time.perf_counter() - t_start
    return {
        "backend": "mss",
        "region": region_name,
        "frames": frames,
        "wall_s": round(wall, 3),
        "fps": round(frames / wall, 2),
        "latency": latency_stats(deltas),
    }


class MouseMover(threading.Thread):
    """Drives desktop activity so DXGI Desktop Duplication presents frames.

    dxcam (DXGI backend) only yields NEW frames when the desktop presents them.
    On a static desktop it returns None. In the real MOONLIGHT scenario the
    running game provides the presentation; in a benchmark we simulate it with
    cursor motion. This is NOT part of the capture cost.
    """

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self._user32 = ctypes.windll.user32

    def run(self) -> None:
        i = 0
        while not self._stop.is_set():
            self._user32.SetCursorPos(500 + (i % 40) * 15, 350 + ((i // 40) % 20) * 15)
            i += 1
            time.sleep(0.01)

    def stop(self) -> None:
        self._stop.set()
        self.join(timeout=2)


def bench_dxcam(region_name: str, duration: float) -> dict:
    """Benchmark dxcam via tight grab() loop (DXGI desktop duplication).

    Counts NEW frames (non-None returns) and per-frame latency. New frames
    require desktop activity, so we run a MouseMover alongside (simulating a
    rendering game). new_frame_fps reflects the desktop presentation rate
    dxcam can follow; per-frame latency is dxcam's intrinsic delivery cost.
    """
    import dxcam

    x, y, r, b = region_for(region_name)
    mover = MouseMover()
    mover.start()
    time.sleep(0.6)
    cam = dxcam.create(device_idx=0, output_idx=0, region=(x, y, r, b),
                       backend="dxgi", output_color="RGB")
    deltas: list[float] = []
    frames = 0
    calls = 0

    try:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < WARMUP:
            if cam.grab() is not None:
                pass
        t_start = time.perf_counter()
        end = t_start + duration
        while time.perf_counter() < end:
            t_grab_start = time.perf_counter()
            f = cam.grab()  # new_frame_only=True: None if no new desktop frame
            now = time.perf_counter()
            calls += 1
            if f is not None:
                frames += 1
                deltas.append(now - t_grab_start)
    finally:
        with contextlib.suppress(Exception):
            cam.release()
        mover.stop()
    wall = time.perf_counter() - t_start
    return {
        "backend": "dxcam",
        "region": region_name,
        "mode": "tight-grab, desktop-active (MouseMover simulating a game)",
        "new_frames": frames,
        "grab_calls": calls,
        "wall_s": round(wall, 3),
        "new_frame_fps": round(frames / wall, 2),
        "grab_call_rate": round(calls / wall, 2),
        "latency_new_frame": latency_stats(deltas),
        "note": "new_frame_fps tracks desktop presentation rate; latency is per delivered frame",
    }


def find_llm() -> psutil.Process | None:
    for p in psutil.process_iter(["pid", "name"]):
        try:
            name = p.info["name"]
            if name and "llama-server" in name.lower() and p.memory_info().rss > 1e9:
                return p  # the real model server, not the launcher
        except psutil.Error:
            continue
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=5.0)
    a = ap.parse_args()

    me = psutil.Process()
    llm = find_llm()

    # snapshot system RAM before
    sys_mem_before = psutil.virtual_memory()

    gpu = GpuSampler()
    gpu.start()

    results: dict = {
        "machine": {
            "cpu": f"AMD Ryzen 7 7800X3D (8C/16T) @ {psutil.cpu_freq().current:.0f}MHz",
            "ram_gb": round(sys_mem_before.total / 1e9, 1),
            "gpu": "NVIDIA GeForce RTX 4090 (24564 MiB)",
            "primary_monitor": f"{PRIMARY['width']}x{PRIMARY['height']}",
        },
        "llm_loaded": llm is not None,
        "llm_pid": llm.pid if llm else None,
        "llm_rss_gb": round(llm.memory_info().rss / 1e9, 2) if llm else None,
        "phases": [],
    }

    def run_phase(name: str, fn, *args) -> dict:
        # reset CPU counters so interval measurement starts here
        me.cpu_percent(None)
        psutil.cpu_percent(None)
        if llm:
            llm.cpu_percent(None)
        time.sleep(0.2)

        t0 = time.monotonic()
        result = fn(*args)
        t1 = time.monotonic()

        time.sleep(0.2)
        result["phase_window_s"] = round(t1 - t0, 2)
        result["cpu_process_percent"] = round(me.cpu_percent(None), 1)
        result["cpu_system_percent"] = round(psutil.cpu_percent(None), 1)
        if llm:
            result["cpu_llm_percent"] = round(llm.cpu_percent(None), 1)
        # GPU + VRAM for this window
        result["gpu"] = gpu.window(t0, t1)
        return result

    for region_name in ("1280x960", "1280x720", "1024x768"):
        results["phases"].append(run_phase(f"mss-{region_name}", bench_mss, region_name, a.duration))
    for region_name in ("1280x960", "1280x720", "1024x768"):
        results["phases"].append(run_phase(f"dxcam-{region_name}", bench_dxcam, region_name, a.duration))

    gpu.stop()

    sys_mem_after = psutil.virtual_memory()
    results["system_ram"] = {
        "total_gb": round(sys_mem_after.total / 1e9, 1),
        "used_gb": round(sys_mem_after.used / 1e9, 1),
        "available_gb": round(sys_mem_after.available / 1e9, 1),
        "percent": sys_mem_after.percent,
    }
    results["llm_rss_gb_after"] = round(llm.memory_info().rss / 1e9, 2) if llm else None

    out = Path(__file__).parent / "capture_bench_results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
