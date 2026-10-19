# tests/hil/ — hardware-in-the-loop tests (T18)

Latency, click accuracy, and serial-loss safety for the Arduino input path.

## Contents
- `mock_serial.py` — in-test emulation of the Arduino (PONG heartbeat, HID commands).
- `test_click_latency.py`, `test_click_accuracy.py`, `test_serial_loss_release.py` —
  the 9 offline HIL tests (mocked serial, no device needed).
- `tools/hil_harness.py` — the real-device harness (COM5): latency p95, click accuracy,
  release-all on serial loss.

## Run
```bash
python -m pytest tests/hil -q          # offline, no hardware
python tools/hil_harness.py            # real Leonardo on COM5 (T15 wiring required)
```

Last real-device result: latency p95 2.0 ms, accuracy + safety PASS (Oct 18).
