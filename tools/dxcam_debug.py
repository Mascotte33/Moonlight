import ctypes
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()
import dxcam

print("dxcam", dxcam.__version__)

# Try full screen first
for region in [None, (0,0,1280,960), (640,240,1920,1200)]:
    print(f"\n=== region={region} ===")
    try:
        cam = dxcam.create(device_idx=0, output_idx=0, region=region, backend="dxgi")
        for i in range(5):
            f = cam.grab(new_frame_only=False)
            print(f"  grab{i}: type={type(f).__name__}",
                  ("shape="+str(f.shape)+" size="+str(f.size)) if f is not None else "")
            time.sleep(0.2)
        cam.release()
    except Exception as e:
        print("  ERROR:", repr(e))
