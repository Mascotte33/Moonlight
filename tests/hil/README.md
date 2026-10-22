# tests/hil/ - hardware-in-the-loop tests (T18)

Latency, click accuracy and serial-loss safety for the Arduino input path.

## Files

| File | What it checks | Tests |
|------|----------------|-------|
| `mock_serial.py` | Fake Arduino: PONG heartbeat and HID commands | - |
| `test_click_latency.py` | A click reaches the wire fast enough | 2 |
| `test_click_accuracy.py` | A click sends the exact wire commands (left and right) | 4 |
| `test_serial_loss_release.py` | On serial loss the host fails closed and releases everything when the board is back | 3 |

These run offline with the mock. `tools/hil_harness.py` runs the same three
scenarios on the real Leonardo (COM5).

## Run

```bash
python -m pytest tests/hil -q    # offline, no hardware
python tools/hil_harness.py      # real Leonardo on COM5
```
