"""Keep the local llama-server actively generating tokens for DURATION seconds.

Used to simulate real contention: MOONLIGHT's capture loop runs at 20 Hz while
the LLM is making decisions (generating tokens concurrently). This script fires
continuous streaming completions so the GPU is doing LLM inference work at the
same time as the capture benchmark runs.

Usage:
    python llm_load.py --duration 40 --port 53925
"""
import argparse
import json
import subprocess
import sys
import time


def gen_once(port: int, model: str) -> int:
    """Send one streaming chat completion; return completion tokens (best-effort)."""
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": "Write a very detailed essay about the history of computing. Be long and thorough."}
        ],
        "max_tokens": 800,
        "temperature": 0.8,
        "stream": True,
    }
    try:
        proc = subprocess.run(
            [
                "curl", "-s", "-N",
                f"http://127.0.0.1:{port}/v1/chat/completions",
                "-H", "Content-Type: application/json",
                "-d", json.dumps(payload),
            ],
            capture_output=True, text=True, timeout=180,
        )
    except Exception:
        return 0
    # streaming: count data lines (SSE) as a proxy for generated tokens
    tok = 0
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("data:") and line[5:].strip() != "[DONE]":
            tok += 1
    return tok


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--port", type=int, default=53925)
    ap.add_argument("--model", type=str, default="")
    a = ap.parse_args()

    # discover model name
    if not a.model:
        out = subprocess.run(["curl", "-s", f"http://127.0.0.1:{a.port}/v1/models"],
                             capture_output=True, text=True)
        try:
            a.model = json.loads(out.stdout)["data"][0]["id"]
        except Exception:
            print("could not resolve model name", file=sys.stderr)
            sys.exit(1)

    end = time.time() + a.duration
    total_tok = 0
    n_req = 0
    while time.time() < end:
        total_tok += gen_once(a.port, a.model)
        n_req += 1
        if time.time() >= end:
            break
    print(json.dumps({"llm_load_seconds": round(a.duration, 1),
                      "requests": n_req,
                      "approx_stream_lines": total_tok}))


if __name__ == "__main__":
    main()
