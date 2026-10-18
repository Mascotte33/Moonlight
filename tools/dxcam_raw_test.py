import contextlib
import ctypes
import os
import threading
import time

with contextlib.suppress(Exception):
    ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)

LOG = os.path.join(os.path.dirname(__file__), "dxcam_raw.log")
def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        f.flush()
open(LOG, "w").close()

def watchdog():
    time.sleep(22)
    log("WATCHDOG force exit")
    os._exit(3)
threading.Thread(target=watchdog, daemon=True).start()

log("importing dxcam")
import comtypes  # noqa: E402  (must follow DPI setup)
import dxcam  # noqa: E402  (must follow DPI setup)

log("creating camera to access internals")
cam = dxcam.create(device_idx=0, output_idx=0, region=(640,240,1920,1200), backend="dxgi")
log("camera created, inspecting internals")
attr = None
for name in ["_duplicator", "duplicator", "_dup"]:
    if hasattr(cam, name):
        attr = name
        break
log(f"duplicator attr: {attr}")
if attr is None:
    log("attrs: " + str([a for a in dir(cam) if not a.startswith('__')]))
    os._exit(4)
dup = getattr(cam, attr)
log(f"dup type={type(dup).__name__}")

user32 = ctypes.windll.user32
stop = False
def mover():
    i = 0
    while not stop:
        user32.SetCursorPos(500 + (i%30)*20, 400 + ((i//30)%10)*20)
        i += 1
        time.sleep(0.02)
threading.Thread(target=mover, daemon=True).start()
time.sleep(1.0)

from dxcam._libs.dxgi import DXGI_OUTDUPL_FRAME_INFO, IDXGIResource  # noqa: E402
from dxcam.core.dxgi_duplicator import DXGI_ERROR_WAIT_TIMEOUT_U32  # noqa: E402

log("polling AcquireNextFrame(100ms) for 8s with mouse motion")
acquired = 0
timeouts = 0
errors = 0
t0 = time.perf_counter()
end = t0 + 8.0
last = None
while time.perf_counter() < end:
    info = DXGI_OUTDUPL_FRAME_INFO()
    res = ctypes.POINTER(IDXGIResource)()
    try:
        dup.duplicator.AcquireNextFrame(100, ctypes.byref(info), ctypes.byref(res))
        acquired += 1
        last = (info.LastPresentTime, info.AccumulatedFrames)
        with contextlib.suppress(Exception):
            dup.duplicator.ReleaseFrame()
    except comtypes.COMError as ce:
        h = (ce.hresult & 0xFFFFFFFF) if ce.hresult is not None else 0
        if h == DXGI_ERROR_WAIT_TIMEOUT_U32:
            timeouts += 1
        else:
            errors += 1
            if errors <= 5:
                log(f"  COMError hresult={h:#010x} (errors so far {errors})")
    if acquired % 100 == 0:
        log(f"  acquired={acquired} timeouts={timeouts} errors={errors} last={last}")
wall = time.perf_counter() - t0
stop = True
log(f"RESULT: acquired={acquired} new frames, timeouts={timeouts}, errors={errors} over {wall:.1f}s => {acquired/wall:.1f} new-frames/s")
try:
    cam.release()
except Exception as e:
    log(f"release err {e}")
log("done")
os._exit(0)
