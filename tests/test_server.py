"""HTTP smoke tests: real server on a free port, MOCK agent and SAMPLE Qloo."""
import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from placecard import presets, server

BODY = {"guests": presets.PRESETS["conference"]["guests"], "table_size": 6,
        "constraints_text": presets.PRESETS["conference"]["constraints"]}


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {"LLM_PROVIDER": "mock", "QLOO_API_KEY": "", "MOCK_DELAY_MS": "0"})
        cls.env.start()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.env.stop()

    def setUp(self):
        server.LIMITER.hits.clear()
        server.LIMITER.day_count = 0

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        return urllib.request.urlopen(req, timeout=30)

    def test_health_reports_modes_without_secrets(self):
        h = json.loads(urllib.request.urlopen(self.base + "/api/health").read())
        self.assertEqual((h["qloo"], h["llm_provider"], h["sample_data"]), ("sample", "mock", True))
        self.assertNotIn("key", json.dumps(h).lower().replace("max_guests", ""))

    def test_static_ui_and_branding(self):
        html = urllib.request.urlopen(self.base + "/").read().decode()
        self.assertIn("Placecard", html)
        self.assertNotIn("TasteTable", html)
        self.assertEqual(urllib.request.urlopen(self.base + "/app.js").status, 200)
        self.assertEqual(urllib.request.urlopen(self.base + "/style.css").status, 200)

    def test_presets_endpoint(self):
        p = json.loads(urllib.request.urlopen(self.base + "/api/presets").read())
        self.assertEqual(set(p), {"wedding", "conference", "mystery"})
        self.assertEqual(len(p["wedding"]["guests"]), 40)
        self.assertEqual(len(p["conference"]["guests"]), 24)

    def test_plan_json(self):
        out = json.loads(self.post("/api/plan", BODY).read())
        self.assertEqual(out["status"], "plan")
        self.assertTrue(out["mode"]["sample_data"])
        self.assertEqual(len(out["plan"]["tables"]), 4)

    def test_stream_is_ndjson_events_then_result(self):
        lines = [json.loads(line) for line in self.post("/api/plan/stream", BODY).read().decode().splitlines() if line]
        self.assertEqual(lines[0]["event"]["type"], "start")
        self.assertIn("result", lines[-1])
        self.assertNotIn("steps", lines[-1]["result"])   # events were already streamed
        self.assertTrue(any(line.get("event", {}).get("name") == "seat_guests" for line in lines))
        self.assertTrue(any(line.get("event", {}).get("type") == "progress" for line in lines))

    def test_bad_input_is_400(self):
        for body in ({"guests": []}, {"guests": [{"name": "A", "favorites": ["x"]}]},
                     {"guests": [{"name": "A", "favorites": ["x"]}, {"name": "a", "favorites": ["y"]}]}):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post("/api/plan", body)
            self.assertEqual(cm.exception.code, 400)

    def test_too_large_body_is_400(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/plan", {"guests": [], "pad": "x" * 200_000})
        self.assertEqual(cm.exception.code, 400)

    def test_rate_limit_is_429(self):
        server.LIMITER.per_ip_hour, old = 1, server.LIMITER.per_ip_hour
        try:
            self.post("/api/plan", BODY).read()
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post("/api/plan", BODY)
            self.assertEqual(cm.exception.code, 429)
        finally:
            server.LIMITER.per_ip_hour = old


class Limiter(unittest.TestCase):
    def test_hourly_window_and_daily_cap(self):
        now = [0.0]
        lim = server.RateLimiter(2, 3, clock=lambda: now[0])
        self.assertTrue(lim.allow("a")[0] and lim.allow("a")[0])
        self.assertFalse(lim.allow("a")[0])
        now[0] = 3601
        self.assertTrue(lim.allow("a")[0])
        self.assertFalse(lim.allow("b")[0])  # daily cap of 3 reached


if __name__ == "__main__":
    unittest.main()
