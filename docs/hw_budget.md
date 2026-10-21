# MOONLIGHT — T02 Hardware Budget Study: Capture FPS + CPU/GPU Contention

**Date:** 2026-10-13 · **Agent:** hw_research · **Machine:** live measurement (Windows 11)

All numbers below are **real measurements** taken on this machine via
benchmark scripts (`bench_capture.py`, `llm_load.py`, since removed; see git
history) and `nvidia-smi`/`psutil`.

---

## 1. System (as measured)

| Resource | Value |
|---|---|
| CPU | AMD Ryzen 7 7800X3D (8C / 16T) @ 4201 MHz |
| RAM | 67.8 GB total · ~33.6 GB used · ~34.2 GB available |
| GPU | NVIDIA GeForce RTX 4090 — 24,564 MiB VRAM |
| Primary monitor | 2560 × 1440 |
| Local LLM (running) | `Qwen3.8-27B-…-Q4_K_M` via llama-server (PID 26444) |
| LLM VRAM footprint | ~20.5–21 GB (leaves ~3–4 GB free of 24.5 GB) |
| LLM RSS | ~7.5 GB idle, ~11 GB while generating (KV cache) |
| LLM throughput (measured) | ~21–22 tokens/s |

> **Model-size note.** The task asked about a *Qwen ~5B (Q5_K_M)*. The model
> actually loaded on this machine is the **27B Q4_K_M** variant, which is
> ~4× heavier on VRAM (~21 GB vs. ~3–4 GB for a 5B). Every contention result
> below is therefore **conservative / worst-case**: a ~5B model would use far
> less VRAM and put far less load on the GPU, so the headroom numbers only get
> better.

---

## 2. Capture FPS — mss (GDI BitBlt)

Tight-loop capture of a client-window-sized region (1280×960 = classic 4:3
Tibia client), DPI-aware, physical pixels.

| Region | FPS (LLM idle) | p99 latency (LLM idle) | FPS (LLM generating) | p99 latency (LLM generating) |
|---|---|---|---|---|
| **1280×960** | **136.6** | 13.1 ms | **87.4** | 20.1 ms |
| 1280×720 | 136.0 | 11.8 ms | 101.6 | 16.6 ms |
| 1024×768 | 140.2 | 11.0 ms | 104.1 | 15.8 ms |

- In a fully idle/isolated run (GPU ~0%), 1280×960 reached **211 FPS**.
- The 1280×960 figure is the one that matters (the actual client window).
- **Even the worst measured case — LLM actively generating — is 87.4 FPS**,
  which is **4.4× the 20 Hz requirement**, with p99 latency 20.1 ms
  (well under the 50 ms budget of 1/20 s).
- mss always returns a frame (current screen content), so it never starves.

**GPU/CPU cost of mss capture itself:** the capture process used ~17–34% of one
core (mss is CPU-bound via GDI); the LLM was ~0% CPU when idle and ~700–770%
of all cores while generating.

---

## 3. Capture FPS — dxcam (DXGI Desktop Duplication)

### Is it installable? — **YES.**
`dxcam 0.3.0` is installed and importable (`import dxcam` succeeds). OpenCV
(`opencv-python`) was installed as its runtime dependency. **No install
failure occurred.**

### Does it work? — **YES, with a behavioral caveat.**

Initial tests appeared to return 0 frames / hang. Root cause found by reading
dxcam's source (`dxcam/core/dxgi_duplicator.py`): the DXGI backend only
delivers **new** frames when the desktop actually presents them. On a static
desktop `AcquireNextFrame` returns `WAIT_TIMEOUT` → `grab()` returns `None`.
A raw `AcquireNextFrame` probe confirmed frames **do** flow once the desktop is
active (~121–194/s). So dxcam works; it just needs a rendering source (which the
real game provides).

With an active desktop (a cursor-motion thread simulates a rendering game in
the benchmark — **not** part of capture cost):

| Region | New-frame FPS (LLM idle) | Per-frame p99 (LLM idle) | New-frame FPS (LLM gen) | Per-frame p99 (LLM gen) |
|---|---|---|---|---|
| **1280×960** | **194.2** | 4.6 ms | **154.6** | 2.0 ms |
| 1280×720 | 188.3 | 5.0 ms | 150.5 | 1.8 ms |
| 1024×768 | 189.9 | 4.9 ms | 152.1 | 1.7 ms |

**Interpretation — read carefully:**
- **Per-frame latency is the real dxcam performance number:** **1.7–5.0 ms**,
  consistently **lower than mss** (which is 11–20 ms). dxcam uses
  GPU-accelerated DXGI duplication; mss uses CPU GDI BitBlt.
- **New-frame FPS tracks the desktop's presentation rate**, not dxcam's
  intrinsic limit. In the benchmark it was driven by the cursor-motion
  thread; in production it will track the **game's actual render rate**
  (60–120+ FPS). dxcam comfortably follows ≥60 FPS.
- **Behavioral caveat:** dxcam returns `None` when there is no *new* desktop
  frame (e.g., a paused/static game). MOONLIGHT must handle this (reuse the
  last frame). mss does not have this issue.

---

## 4. LLM contention (measured, concurrent generation)

Ran the capture benchmark **while** the LLM was actively generating
(`tools/llm_load.py`, ~21 tok/s, max_tokens 800, sustained):

| Metric | LLM idle (baseline) | LLM generating (contention) |
|---|---|---|
| GPU utilization | ~15% | **~50–56%** |
| GPU VRAM used | ~21.2 GB | ~21.0 GB |
| LLM CPU | ~0% | **~700–770%** (of 16 threads) |
| System RAM used | ~36.5 GB | ~33.6 GB |
| mss 1280×960 FPS | 136.6 | **87.4** |
| mss 1280×960 p99 | 13.1 ms | **20.1 ms** |
| dxcam 1280×960 new-fps | 194.2 | **154.6** |
| dxcam 1280×960 per-frame p99 | 4.6 ms | **2.0 ms** |

**Key takeaways:**
- The 27B model, while generating, holds ~21 GB VRAM and ~50–56% GPU util, and
  uses ~700% CPU. This is the **worst-case** contention.
- **mss capture FPS drops 136 → 87** under this load, but that is still
  **4.4× the 20 Hz target**. p99 stays at 20 ms (< 50 ms budget).
- **dxcam's per-frame latency actually stays excellent (2 ms)** and its
  new-frame throughput (154/s) remains far above 20 Hz.
- **VRAM is the tightest resource** with the 27B model: only ~3–4 GB free of
  24.5 GB. A ~5B model would use ~3–4 GB total, freeing ~17 GB — far more
  headroom.
- **RAM is not a constraint:** ~34 GB available with the LLM resident.
- **CPU has headroom:** the 7800X3D has 16 threads; the LLM uses ~7–8 cores
  when busy, leaving ~8 cores free for capture + game.

---

## 5. Recommendation

### ✅ 20 Hz client-window-only capture **is achievable with headroom**, even
with a large local LLM generating concurrently.

- **Worst measured case:** a 27B model generating (GPU ~55%, ~700% CPU) while
  mss captured 1280×960 → **87 FPS**, p99 20 ms. That is **4.4× the 20 Hz
  requirement** with latency **2.5× under budget**.
- With the intended ~5B model (much lighter), headroom grows substantially.

### Which capture backend?

| | mss | dxcam |
|---|---|---|
| 1280×960 FPS (LLM gen) | 87.4 | 154.6 new-fps |
| Per-frame / capture p99 | 20.1 ms | 2.0 ms |
| Always returns a frame? | ✅ Yes | ⚠️ Only when desktop presents |
| GPU accelerated? | ❌ CPU (GDI) | ✅ Yes (DXGI) |
| Complexity / robustness | Simple, robust | Needs "no new frame" handling |

**Recommendation:**
- **Primary backend: mss.** Simplest, most robust, always returns a frame, and
  delivers 87–211 FPS — comfortably >20 Hz even under heavy LLM contention.
  Zero special-casing. This is the safe default for a production game bot.
- **Optimization path: dxcam.** ~10× lower per-frame latency (2 ms vs 20 ms)
  and GPU-accelerated. Worth adopting **if/when** lower latency matters, but
  you must handle the "no new frame" (`None`) case and it only yields frames as
  fast as the game presents them. Start with mss; consider dxcam as a
  follow-up optimization.

### Suggested 20 Hz capture configuration
- Region: 1280×960 (client window), DPI-aware physical pixels.
- Backend: mss.
- Expected: 87+ FPS under full LLM contention (target 20 Hz → ~4× headroom),
  20+ ms worst-case p99 latency still well under the 50 ms budget.
- Monitor VRAM: with a ~5B model (~3–4 GB) the GPU has ~20 GB free — no
  contention risk from capture (mss/dxcam capture adds negligible VRAM).

---

## 6. What failed / caveats (honesty section)

- **dxcam initially appeared broken** (0 frames, hung threaded mode). This was
  **not** an install failure and **not** a bug — it is DXGI Desktop Duplication
  behaving as designed (no new frames on a static desktop). It works correctly
  once the desktop is actively presenting (as a running game would).
- **dxcam "new-frame FPS" numbers are not a pure speed test** — they track the
  desktop's presentation rate (driven by the benchmark's cursor-motion thread).
  Treat them as "can follow ≥60 FPS"; the **per-frame latency (2–5 ms)** is the
  meaningful dxcam performance figure.
- **Model discrepancy:** the task referenced a ~5B (Q5_K_M) model, but this
  machine runs the 27B Q4_K_M. All contention numbers are therefore
  worst-case; a 5B model would show less contention, not more.
- **mss FPS varies with system load** (211 idle → 136 LLM-resident → 87
  LLM-generating). All values far exceed 20 Hz, so the feasibility conclusion
  is robust to this variance.
