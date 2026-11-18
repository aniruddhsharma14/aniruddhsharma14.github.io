"""
Market-data recorder: Kalshi (your execution venue) + Polymarket (read-only reference).

Design principles, deliberate:
  - Capture RAW. Each line stored is the exact message off the wire plus one local
    receive timestamp. No parsing, no normalisation, no field-picking at record time.
    Parsing is reversible offline; a dropped field at capture time is gone forever.
  - Never crash on a bad message or a dropped socket. Log it, reconnect, keep going.
    The whole point is an unbroken tape; a recorder that dies at 3am is worthless.
  - One file per venue per UTC day, append-only JSONL. Easy to reason about, easy to
    replay, survives restarts (it appends, never truncates).

Run it and leave it running from today until the tournament ends:
    python recorder.py

Stop with Ctrl-C; it flushes and closes cleanly.

------------------------------------------------------------------------------------
BEFORE FIRST RUN — three things you must set (see CONFIG):
  1. Kalshi API key id + RSA private-key file. Generate the key pair in the Kalshi
     web UI (Settings -> API keys); it hands you a key id and a .pem private key.
  2. The market tickers you want to record (KALSHI_TICKERS). Pull live World Cup
     tickers from GET /trade-api/v2/markets?series_ticker=... once they're listed.
  3. The Polymarket asset (token) ids for the same contracts (POLY_ASSET_IDS), from
     Polymarket's /markets REST endpoint. Leave empty to record Kalshi only.

Endpoints/auth verified against Kalshi docs as of 2026; if a connection 4xx's at the
handshake, re-check the current endpoint + signing rules in the Kalshi API docs — they
have moved before.
------------------------------------------------------------------------------------
"""

from __future__ import annotations

import asyncio
import os
import base64
import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


# ============================== CONFIG ============================== #

OUT_DIR = Path("./recordings")

# --- Kalshi ---
KALSHI_WS = "wss://api.elections.kalshi.com/trade-api/ws/v2"
KALSHI_WS_PATH = "/trade-api/ws/v2"               # the path you sign for the handshake
KALSHI_KEY_ID = os.environ.get("KALSHI_KEY_ID", "")          # never hardcode it
KALSHI_PRIVATE_KEY_PEM = os.environ.get("KALSHI_PRIVATE_KEY_PEM", "kalshi_private_key.pem")  # path to the .pem file; keep it out of git
KALSHI_CHANNELS = ["orderbook_delta", "trade", "ticker"]
# Tickers are read from this file (maintained by wc_tickers.py), re-read on every
# (re)connect — so refreshing the file + restarting picks up new markets. The recorder
# REFUSES to start with an empty list (an empty subscription = the whole-exchange firehose).
TICKERS_FILE = Path(__file__).resolve().parent / "tickers.json"

# --- Polymarket (public, read-only reference; no account needed) ---
POLY_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLY_ASSET_IDS: list[str] = [
    # token ids from Polymarket /markets; leave empty to skip Polymarket
]

# reconnect backoff
BACKOFF_START = 1.0
BACKOFF_MAX = 60.0


def load_kalshi_tickers() -> list[str]:
    """Read the current ticker list from TICKERS_FILE. Hard-fails on empty/missing,
    because subscribing with no tickers makes Kalshi stream the ENTIRE exchange."""
    try:
        data = json.loads(TICKERS_FILE.read_text())
        tickers = data.get("tickers", [])
    except FileNotFoundError:
        raise SystemExit(f"{TICKERS_FILE} not found — run wc_tickers.py first.")
    if not tickers:
        raise SystemExit("tickers.json has no tickers — refusing to start "
                         "(empty subscription = whole-exchange firehose).")
    return tickers


# ============================== WRITER ============================== #

class DailyJSONLWriter:
    """Append-only JSONL, one file per UTC day: recordings/<venue>-YYYY-MM-DD.jsonl.
    Reopens automatically when the UTC date rolls over."""

    def __init__(self, out_dir: Path, venue: str) -> None:
        self.out_dir = out_dir
        self.venue = venue
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._date = None
        self._fh = None

    def _roll_if_needed(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._date:
            if self._fh:
                self._fh.flush()
                self._fh.close()
            path = self.out_dir / f"{self.venue}-{today}.jsonl"
            self._fh = open(path, "a", buffering=1)  # line-buffered
            self._date = today

    def write(self, raw_message: str) -> None:
        self._roll_if_needed()
        record = {
            "ts_recv_ms": int(time.time() * 1000),  # local receive time, ms
            "venue": self.venue,
            "raw": raw_message,
        }
        self._fh.write(json.dumps(record, separators=(",", ":")) + "\n")

    def close(self) -> None:
        if self._fh:
            self._fh.flush()
            self._fh.close()
            self._fh = None


# ============================== KALSHI AUTH ============================== #

def _load_private_key(pem_path: str):
    with open(pem_path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def kalshi_auth_headers(key_id: str, pem_path: str, method: str, path: str) -> dict:
    """Build the KALSHI-ACCESS-* handshake headers. Signs <timestamp_ms><METHOD><path>
    with RSA-PSS / SHA256, per Kalshi's auth scheme."""
    ts_ms = str(int(time.time() * 1000))
    msg = (ts_ms + method.upper() + path).encode()
    key = _load_private_key(pem_path)
    signature = key.sign(
        msg,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        "KALSHI-ACCESS-TIMESTAMP": ts_ms,
    }


# ============================== RECORDER LOOPS ============================== #

async def _stream(name: str, connect_fn, subscribe_fn, writer: DailyJSONLWriter,
                  stop: asyncio.Event) -> None:
    """Generic connect -> subscribe -> record loop with exponential backoff."""
    backoff = BACKOFF_START
    while not stop.is_set():
        try:
            async with connect_fn() as ws:
                await subscribe_fn(ws)
                backoff = BACKOFF_START  # healthy connection resets backoff
                print(f"[{name}] connected, recording")
                async for message in ws:
                    writer.write(message if isinstance(message, str) else message.decode())
                    if stop.is_set():
                        break
        except asyncio.CancelledError:
            raise
        except Exception as e:  # any drop/error -> log, back off, reconnect
            print(f"[{name}] disconnected ({type(e).__name__}: {e}); "
                  f"reconnecting in {backoff:.0f}s")
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, BACKOFF_MAX)
    print(f"[{name}] stopped")


async def run_kalshi(writer: DailyJSONLWriter, stop: asyncio.Event) -> None:
    def connect_fn():
        headers = kalshi_auth_headers(KALSHI_KEY_ID, KALSHI_PRIVATE_KEY_PEM,
                                      "GET", KALSHI_WS_PATH)
        # websockets>=14 uses additional_headers; older uses extra_headers
        return websockets.connect(KALSHI_WS, additional_headers=headers,
                                  ping_interval=10, ping_timeout=10)

    async def subscribe_fn(ws):
        tickers = load_kalshi_tickers()  # re-read fresh on every (re)connect
        print(f"[kalshi] subscribing to {len(tickers)} tickers")
        await ws.send(json.dumps({
            "id": 1, "cmd": "subscribe",
            "params": {"channels": KALSHI_CHANNELS, "market_tickers": tickers},
        }))

    await _stream("kalshi", connect_fn, subscribe_fn, writer, stop)


async def run_polymarket(writer: DailyJSONLWriter, stop: asyncio.Event) -> None:
    def connect_fn():
        return websockets.connect(POLY_WS, ping_interval=10, ping_timeout=10)

    async def subscribe_fn(ws):
        await ws.send(json.dumps({"assets_ids": POLY_ASSET_IDS, "type": "market"}))

    await _stream("polymarket", connect_fn, subscribe_fn, writer, stop)


# ============================== MAIN ============================== #

async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass  # signal handlers unavailable (e.g. Windows) -> Ctrl-C still raises

    tasks = []
    k_writer = DailyJSONLWriter(OUT_DIR, "kalshi")
    tasks.append(asyncio.create_task(run_kalshi(k_writer, stop)))

    p_writer = None
    if POLY_ASSET_IDS:
        p_writer = DailyJSONLWriter(OUT_DIR, "polymarket")
        tasks.append(asyncio.create_task(run_polymarket(p_writer, stop)))

    print("Recorder running. Ctrl-C to stop.")
    await stop.wait()
    print("\nShutting down ...")
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    k_writer.close()
    if p_writer:
        p_writer.close()
    print("Clean shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())