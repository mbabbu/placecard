"""LLM provider adapters, with fake transports (no keys, no network)."""
import io
import json
import os
import unittest
import urllib.error
from types import SimpleNamespace as NS
from unittest import mock

from placecard import config
from placecard.llm import get_llm
from placecard.llm.anthropic_llm import AnthropicLLM
from placecard.llm.base import LLMError, ToolCall
from placecard.llm.gemini_llm import GeminiLLM
from placecard.tools import TOOL_SPECS


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class Gemini(unittest.TestCase):
    def test_request_body_and_tool_call_round_trip(self):
        sent = []
        reply = {"candidates": [{"content": {"role": "model", "parts": [
            {"text": "Looking up favourites."},
            {"functionCall": {"name": "build_taste_profiles", "args": {"names": ["Mei"]}, "id": "fc1"},
             "thoughtSignature": "SIG=="}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 5}}

        def opener(req, timeout=0):
            sent.append(req)
            return FakeResp(json.dumps(reply).encode())

        g = GeminiLLM("GKEY", "gemini-3.8-flash", opener=opener, sleep=lambda s: None)
        t = g.chat("SYS", [{"role": "user", "content": "hi"}], TOOL_SPECS)
        req = sent[0]
        self.assertIn("/v1beta/models/gemini-3.8-flash:generateContent", req.full_url)
        self.assertEqual(req.get_header("X-goog-api-key"), "GKEY")
        body = json.loads(req.data)
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "SYS")
        decls = body["tools"][0]["functionDeclarations"]
        self.assertEqual({d["name"] for d in decls}, {s["name"] for s in TOOL_SPECS})
        self.assertIn("parametersJsonSchema", decls[0])
        self.assertEqual(t.tool_calls[0].id, "fc1")
        self.assertEqual((t.text, t.input_tokens, t.output_tokens), ("Looking up favourites.", 100, 25))

        # Second turn: model content is replayed verbatim (keeps thoughtSignature) and results go back as functionResponse.
        hist = [{"role": "user", "content": "hi"},
                {"role": "assistant", "text": t.text, "tool_calls": t.tool_calls, "raw": t.raw},
                {"role": "tool", "results": [{"id": "fc1", "name": "build_taste_profiles", "content": '{"results":[]}',
                                              "is_error": False}]}]
        conv = GeminiLLM.convert(hist)
        self.assertEqual(conv[1]["role"], "model")
        self.assertEqual(conv[1]["parts"][1]["thoughtSignature"], "SIG==")
        fr = conv[2]["parts"][0]["functionResponse"]
        self.assertEqual(fr, {"name": "build_taste_profiles", "response": {"result": {"results": []}}, "id": "fc1"})

    def test_429_retries_then_raises(self):
        calls = []

        def opener(req, timeout=0):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 429, "quota", {}, io.BytesIO(b'{"error":{"message":"quota"}}'))

        g = GeminiLLM("K", opener=opener, sleep=lambda s: None)
        with self.assertRaises(LLMError) as cm:
            g.chat("s", [{"role": "user", "content": "x"}], TOOL_SPECS)
        self.assertTrue(cm.exception.retryable)
        self.assertEqual(len(calls), 4)  # 1 try + 3 backoff retries

    def test_bad_key_is_fatal(self):
        def opener(req, timeout=0):
            raise urllib.error.HTTPError(req.full_url, 403, "denied", {}, io.BytesIO(b"{}"))

        with self.assertRaises(LLMError) as cm:
            GeminiLLM("K", opener=opener, sleep=lambda s: None).chat("s", [{"role": "user", "content": "x"}], [])
        self.assertTrue(cm.exception.fatal)


class Anthropic(unittest.TestCase):
    def test_messages_create_shape_and_parsing(self):
        captured = {}
        content = [NS(type="text", text="Searching."),
                   NS(type="tool_use", id="tu1", name="build_taste_profiles", input={"names": ["Mei"]})]

        class Msgs:
            def create(self, **kw):
                captured.update(kw)
                return NS(content=content, stop_reason="tool_use", usage=NS(input_tokens=50, output_tokens=9))

        llm = AnthropicLLM(model="claude-haiku-4-5", client=NS(messages=Msgs()))
        hist = [{"role": "user", "content": "hi"},
                {"role": "assistant", "text": "", "tool_calls": [ToolCall("tu0", "seat_guests", {"seed": 3})], "raw": None},
                {"role": "tool", "results": [{"id": "tu0", "name": "seat_guests", "content": "{}", "is_error": True}]}]
        t = llm.chat("SYS", hist, TOOL_SPECS)
        self.assertEqual(captured["model"], "claude-haiku-4-5")
        self.assertEqual(captured["system"], "SYS")
        self.assertEqual(captured["tools"][0]["input_schema"], TOOL_SPECS[0]["parameters"])
        msgs = captured["messages"]
        self.assertEqual(msgs[1]["content"][0], {"type": "tool_use", "id": "tu0", "name": "seat_guests", "input": {"seed": 3}})
        self.assertEqual(msgs[2]["content"][0], {"type": "tool_result", "tool_use_id": "tu0", "content": "{}", "is_error": True})
        self.assertEqual((t.text, t.tool_calls[0].name, t.tool_calls[0].args), ("Searching.", "build_taste_profiles", {"names": ["Mei"]}))
        self.assertEqual((t.input_tokens, t.output_tokens, t.stop), (50, 9, "tool_use"))


class Selection(unittest.TestCase):
    def test_auto_selection_prefers_anthropic_then_gemini_then_mock(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "", "ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "",
                                          "GOOGLE_API_KEY": ""}):
            self.assertEqual(config.llm_provider(), "mock")
            self.assertEqual(get_llm().provider, "mock")
            os.environ["GEMINI_API_KEY"] = "g"
            self.assertEqual(config.llm_provider(), "gemini")
            os.environ["ANTHROPIC_API_KEY"] = "a"
            self.assertEqual(config.llm_provider(), "anthropic")
            os.environ["LLM_PROVIDER"] = "gemini"
            self.assertEqual(config.llm_provider(), "gemini")
            self.assertEqual(get_llm().label, "Gemini (gemini-3.5-flash)")

    def test_explicit_provider_without_key_fails_clearly(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": ""}):
            with self.assertRaises(LLMError):
                get_llm()


if __name__ == "__main__":
    unittest.main()
