"""Quick probe: monitors, llama-server footprint, GPU state."""
import ctypes
import json
import subprocess

# DPI awareness so geometry is in physical pixels
try:
    ctypes.windll.shcore.SetProcessDpiAwarenessContext(-4)  # PER_MONITOR_AWARE_V2
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

import mss
import psutil


def main() -> None:
    with mss.mss() as sct:
        mons = [
            {
                "index": i,
                "left": m["left"],
                "top": m["top"],
                "width": m["width"],
                "height": m["height"],
                "monitors": "primary" if m == sct.monitors[0] else "secondary",
            }
            for i, m in enumerate(sct.monitors)
        ]
    print(json.dumps({"monitors": mons}, indent=1))

    # Find llama-server
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = p.info
            if info["name"] and ("llama" in info["name"].lower() or "ollama" in info["name"].lower()):
                with p.oneshot():
                    mem = p.memory_info()
                    cpu = p.cpu_percent(interval=0.5)
                print(json.dumps({
                    "llm_process": {
                        "pid": info["pid"],
                        "name": info["name"],
                        "cmdline": (info["cmdline"] or [])[:6],
                        "rss_gb": round(mem.rss / 1e9, 2),
                        "cpu_percent": round(cpu, 1),
                    }
                }, indent=1))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,power.draw",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, check=True)
    print("nvidia-smi:", out.stdout.strip())

if __name__ == "__main__":
    main()
