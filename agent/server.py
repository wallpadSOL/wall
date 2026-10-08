"""HTTP: the files the site reads, the clips, and one POST that launches.

GET  /state.json   /spikes.json   /clips.json   /coins.json   /fees.json   /trending.json   /events.json
GET  /clips/<file>
POST /launch       json: stream, name, ticker, description, image_b64, mime,
                         payer, payment_sig

Everything else is 404. CORS is open for GET and for POST from the site.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

log = logging.getLogger("wall.server")

PUBLIC_FILES = {"state.json", "spikes.json", "clips.json", "coins.json", "fees.json", "launch.json", "trending.json", "events.json"}
MAX_BODY = 2_500_000  # 2.5 MB: a coin image plus json


class Store:
    """Shared between the agent loop and request threads."""

    def __init__(self, data_dir: str, launcher=None):
        self.data_dir = data_dir
        self.launcher = launcher
        self.lock = threading.Lock()
        self.rate: dict[str, list[float]] = {}

    def path(self, name: str) -> str:
        return os.path.join(self.data_dir, name)

    def read(self, name: str, default):
        try:
            with open(self.path(name)) as f:
                return json.load(f)
        except Exception:
            return default

    def write(self, name: str, doc) -> None:
        tmp = self.path(name) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f, separators=(",", ":"))
        os.replace(tmp, self.path(name))

    def add_coin(self, rec: dict) -> None:
        with self.lock:
            coins = self.read("coins.json", [])
            coins.append(rec)
            self.write("coins.json", coins)

    def allow(self, ip: str, per_min: int = 10) -> bool:
        now = time.time()
        with self.lock:
            hits = [t for t in self.rate.get(ip, []) if now - t < 60]
            if len(hits) >= per_min:
                self.rate[ip] = hits
                return False
            hits.append(now)
            self.rate[ip] = hits
            return True


def make_handler(store: Store, cors: str = "*"):
    class H(BaseHTTPRequestHandler):
        server_version = "wall/0.1"

        def log_message(self, fmt, *args):  # quieter
            log.debug(fmt, *args)

        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", cors)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def _json(self, code: int, doc) -> None:
            body = json.dumps(doc).encode()
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            p = urlparse(self.path).path
            name = p.strip("/")
            if name in PUBLIC_FILES:
                fp = store.path(name)
                if not os.path.exists(fp):
                    return self._json(200, [] if name != "state.json" else {"updated": 0, "streams": []})
                with open(fp, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                return self.wfile.write(body)
            if name.startswith("clips/") and ".." not in name:
                fp = store.path(name)
                if not os.path.isfile(fp):
                    return self._json(404, {"error": "no such clip"})
                ctype = "video/mp4" if fp.endswith(".mp4") else "image/jpeg"
                size = os.path.getsize(fp)
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                with open(fp, "rb") as f:
                    while chunk := f.read(65536):
                        self.wfile.write(chunk)
                return None
            if name == "" or name == "health":
                return self._json(200, {"ok": True, "ts": int(time.time())})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            p = urlparse(self.path).path
            if p != "/launch":
                return self._json(404, {"error": "not found"})
            ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
            if not store.allow(ip):
                return self._json(429, {"error": "too many requests"})
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > MAX_BODY:
                return self._json(413, {"error": "body too large"})
            try:
                body = json.loads(self.rfile.read(n))
            except Exception:
                return self._json(400, {"error": "bad json"})
            if store.launcher is None:
                return self._json(503, {"error": "launching is not enabled on this agent yet"})
            try:
                img = base64.b64decode(body.get("image_b64") or "", validate=True)
            except Exception:
                return self._json(400, {"error": "image_b64 is not base64"})
            if not (1000 <= len(img) <= 1_800_000):
                return self._json(400, {"error": "image must be 1KB to 1.8MB"})
            mime = body.get("mime") or "image/png"
            if mime not in ("image/png", "image/jpeg", "image/gif", "image/webp"):
                return self._json(400, {"error": "image must be png, jpg, gif or webp"})
            try:
                rec = store.launcher.create(
                    stream_key=str(body.get("stream") or "")[:80],
                    name=body.get("name") or "", ticker=body.get("ticker") or "",
                    description=body.get("description") or "", image=img, mime=mime,
                    payer=str(body.get("payer") or "")[:64],
                    payment_sig=str(body.get("payment_sig") or "")[:128],
                )
            except Exception as e:  # noqa: BLE001
                log.warning("launch refused: %s", e)
                return self._json(400, {"error": str(e)})
            store.add_coin(rec)
            return self._json(200, {"ok": True, "coin": rec})

    return H


def serve(store: Store, host: str, port: int, cors: str = "*") -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer((host, port), make_handler(store, cors))
    t = threading.Thread(target=srv.serve_forever, daemon=True, name="wall-http")
    t.start()
    log.info("http on %s:%s", host, port)
    return srv
