import contextlib
import ctypes
import os
import threading
import time

with contextlib.suppress(Exception):
    ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)

LOG = os.path.join(os.path.dirname(__file__), "dxcam_fps.log")
def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        f.flush()
open(LOG, "w").close()

def watchdog():
    time.sleep(25)
    log("WATCHDOG force exit")
    os._exit(3)
threading.Thread(target=watchdog, daemon=True).start()

log("importing dxcam")
import dxcam  # noqa: E402  (must follow DPI setup)

# force desktop activity continuously
user32 = ctypes.windll.user32
stop = False
def mover():
    i = 0
    while not stop:
        user32.SetCursorPos(500 + (i%40)*15, 350 + ((i//40)%20)*15)
        i += 1
        time.sleep(0.01)
threading.Thread(target=mover, daemon=True).start()
time.sleep(0.8)

def bench_region(label, region, dur=5.0):
    cam = dxcam.create(device_idx=0, output_idx=0, region=region, backend="dxgi")
    # warmup
    got0 = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.8:
        if cam.grab() is not None:
            got0 += 1
    # measure: count new (non-None) frames via tight grab() loop
    frames = 0
    calls = 0
    deltas = []
    t_start = time.perf_counter()
    end = t_start + dur
    while time.perf_counter() < end:
        g0 = time.perf_counter()
        f = cam.grab()   # new_frame_only=True default; None if no new desktop frame
        now = time.perf_counter()
        calls += 1
        if f is not None:
            frames += 1
            deltas.append(now - g0)
    cam.release()
    wall = time.perf_counter() - t_start
    def pctl(a, p):
        if not a:
            return 0.0
        s = sorted(a)
        k = (len(s) - 1) * p / 100.0
        f0 = int(k)
        c = min(f0 + 1, len(s) - 1)
        return s[f0] + (s[c] - s[f0]) * (k - f0)
    return {
        "region": label,
        "warmup_new_frames": got0,
        "new_frames": frames,
        "grab_calls": calls,
        "wall_s": round(wall,2),
        "new_frame_fps": round(frames/wall,1),
        "grab_call_rate": round(calls/wall,1),
        "p50_ms": round(pctl(deltas,50)*1000,2),
        "p99_ms": round(pctl(deltas,99)*1000,2),
    }

results = []
results.append(bench_region("1280x960 @ (640,240,1920,1200)", (640,240,1920,1200)))
results.append(bench_region("1280x720 @ (640,360,1920,1080)", (640,360,1920,1080)))
log(f"RESULTS: {results}")

stop = True
log("done")
os._exit(0)
