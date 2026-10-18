import ctypes
import os
import sys
import threading
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

LOG = os.path.join(os.path.dirname(__file__), "dxcam_test.log")
def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        f.flush()
    sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stdout.flush()

# clear log
open(LOG, "w").close()

# watchdog: force-exit after 25s regardless
def watchdog():
    time.sleep(25)
    log("WATCHDOG: force exit after 25s")
    os._exit(3)
threading.Thread(target=watchdog, daemon=True).start()

log("importing dxcam")
import dxcam  # noqa: E402  (must follow DPI setup)

log("dxcam imported")

log("creating cam region=(640,240,1920,1200)")
cam = dxcam.create(device_idx=0, output_idx=0, region=(640,240,1920,1200),
                   backend="dxgi", output_color="RGB")
log("cam created")

log("calling start(target_fps=60, video_mode=True)")
cam.start(target_fps=60, video_mode=True)
log(f"start() returned, is_capturing={cam.is_capturing}")

time.sleep(1.0)
log("warmup done, getting frames for 3s")
frames = 0
t0 = time.perf_counter()
end = t0 + 3.0
iters = 0
while time.perf_counter() < end:
    f = cam.get_latest_frame(copy=False)
    iters += 1
    if f is not None:
        frames += 1
    if iters % 1000 == 0:
        log(f"  iter {iters}, frames={frames}")
wall = time.perf_counter() - t0
log(f"RESULT: {frames} frames / {iters} calls in {wall:.2f}s = {frames/wall:.1f} FPS (frame_rate), call_rate={iters/wall:.1f}")

log("calling stop()")
cam.stop()
log("stop() returned")
cam.release()
log("released, done")
os._exit(0)
