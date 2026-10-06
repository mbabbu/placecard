"""Stdlib web server: python -m placecard.server   (PORT env, default 8000)

GET  /                  the single-page UI
GET  /api/health        modes (live/sample Qloo, which LLM); never reveals keys
GET  /api/presets       guest lists (wedding, conference dinner, mystery); real titles when Qloo is live, SAMPLE otherwise
POST /api/plan          run the agent, return the full result as JSON
POST /api/plan/stream   run the agent, stream NDJSON events (one per agent step), last line = result
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import agent, config, presets, qloo
from .llm import LLMError, get_llm

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static")
FILES = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
         "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8"),
         "/favicon.svg": ("favicon.svg", "image/svg+xml")}
MAX_BODY = 100_000


class RateLimiter:
    """Per-IP hourly and global daily caps so a public demo can't drain the owner's quota."""

    def __init__(self, per_ip_hour, per_day, clock=time.time):
        self.per_ip_hour, self.per_day, self.clock = per_ip_hour, per_day, clock
        self.hits, self.day, self.day_count = {}, None, 0
        self.lock = threading.Lock()

    def allow(self, ip):
        now = self.clock()
        with self.lock:
            day = int(now // 86400)
            if day != self.day:
                self.day, self.day_count = day, 0
            if self.day_count >= self.per_day:
                return False, "The demo's daily run limit is reached. Please try again tomorrow."
            recent = [t for t in self.hits.get(ip, []) if now - t < 3600]
            if len(recent) >= self.per_ip_hour:
                return False, "You've reached the hourly limit for this demo. Please try again later."
            recent.append(now)
            self.hits[ip] = recent
            self.day_count += 1
            if len(self.hits) > 5000:
                self.hits = {k: v for k, v in self.hits.items() if v and now - v[-1] < 3600}
            return True, ""


LIMITER = RateLimiter(config.RUNS_PER_IP_PER_HOUR, config.RUNS_PER_DAY)


def health():
    provider = config.llm_provider()
    model = {"anthropic": config.anthropic_model(), "gemini": config.gemini_model()}.get(provider, "scripted")
    out = {"ok": True, "qloo": config.qloo_mode(), "sample_data": config.qloo_mode() == "sample",
           "llm_provider": provider, "llm_model": model, "max_guests": config.MAX_GUESTS}
    client = qloo._SHARED.get("client")      # never creates a client or calls Qloo: health is pinged every 10 minutes
    if client is not None and config.qloo_mode() == "live":
        out["qloo_quota"] = client.quota()   # last known monthly quota from Qloo's response headers
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "Placecard/1.0"

    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' https: data:; "
                                                        "style-src 'self' 'unsafe-inline'")
        self.end_headers()
        self.wfile.write(b)

    def _ip(self):
        # The hosting proxy appends the real client IP last; earlier entries can be spoofed.
        fwd = [p.strip() for p in self.headers.get("X-Forwarded-For", "").split(",") if p.strip()]
        return fwd[-1] if fwd else self.client_address[0]

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api/health":
            return self._send(200, health())
        if path == "/api/presets":
            return self._send(200, presets.for_mode(config.qloo_mode() == "live"))
        if path in FILES:
            name, ctype = FILES[path]
            try:
                with open(os.path.join(STATIC, name), "rb") as f:
                    return self._send(200, f.read(), ctype)
            except FileNotFoundError:
                pass
        self._send(404, {"error": "not found"})

    def _read_request(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        if n > MAX_BODY:
            raise ValueError("Request too large.")
        data = json.loads(self.rfile.read(n) or b"{}")
        return agent.normalise_request(data)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path not in ("/api/plan", "/api/plan/stream"):
            return self._send(404, {"error": "not found"})
        try:
            req = self._read_request()
        except (ValueError, TypeError, AttributeError, json.JSONDecodeError) as e:
            return self._send(400, {"error": str(e) if isinstance(e, ValueError) else "Bad request."})
        ok, why = LIMITER.allow(self._ip())
        if not ok:
            return self._send(429, {"error": why})
        try:
            llm = get_llm()
        except LLMError as e:
            return self._send(500, {"error": str(e)})
        client = qloo.get_client()
        if path == "/api/plan":
            return self._send(200, agent.run(llm, client, req))
        # Streaming: newline-delimited JSON, flushed per agent step.
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        lock = threading.Lock()

        def write(obj):
            try:
                with lock:
                    self.wfile.write((json.dumps(obj) + "\n").encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        result = agent.run(llm, client, req, emit=lambda ev: write({"event": ev}))
        result.pop("steps", None)   # the client already received every step as an event
        write({"result": result})

    def log_message(self, fmt, *args):  # keep logs free of request bodies
        if os.environ.get("PLACECARD_ACCESS_LOG"):
            super().log_message(fmt, *args)


def main():
    port = int(os.environ.get("PORT", "8000"))
    h = health()
    print(f"Placecard on http://0.0.0.0:{port}  qloo={h['qloo']}  llm={h['llm_provider']}:{h['llm_model']}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
