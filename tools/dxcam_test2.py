import ctypes
import os
import sys
import threading
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

LOG = os.path.join(os.path.dirname(__file__), "dxcam_test2.log")
def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        f.flush()
    sys.stdout.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    sys.stdout.flush()

open(LOG, "w").close()

def watchdog():
    time.sleep(30)
    log("WATCHDOG: force exit after 30s")
    os._exit(3)
threading.Thread(target=watchdog, daemon=True).start()

# background mouse mover to force desktop presentation
stop_mover = False
def mover():
    user32 = ctypes.windll.user32
    i = 0
    while not stop_mover:
        x = 400 + (i % 20) * 15
        y = 300 + ((i // 20) % 20) * 10
        user32.SetCursorPos(x, y)
        i += 1
        time.sleep(0.01)
threading.Thread(target=mover, daemon=True).start()

log("importing dxcam")
import dxcam  # noqa: E402  (must follow DPI setup)

log("dxcam imported, waiting 1.5s for desktop to have activity")
time.sleep(1.5)

log("creating cam region=(640,240,1920,1200)")
cam = dxcam.create(device_idx=0, output_idx=0, region=(640,240,1920,1200),
                   backend="dxgi", output_color="RGB")
log("cam created, starting target_fps=120 video_mode=True")
cam.start(target_fps=120, video_mode=True)
time.sleep(1.0)

log("getting frames for 4s (mouse moving in background)")
frames = 0
t0 = time.perf_counter()
end = t0 + 4.0
iters = 0
while time.perf_counter() < end:
    f = cam.get_latest_frame(copy=False)
    iters += 1
    if f is not None:
        frames += 1
    if iters % 500 == 0:
        log(f"  iter {iters}, frames={frames}")
wall = time.perf_counter() - t0
log(f"RESULT: {frames} frames / {iters} calls in {wall:.2f}s = {frames/wall:.1f} FPS")

stop_mover = True
log("calling stop()")
try:
    cam.stop()
    log("stop() returned")
except Exception as e:
    log(f"stop() error: {e}")
try:
    cam.release()
    log("released, done")
except Exception as e:
    log(f"release() error: {e}")
os._exit(0)
