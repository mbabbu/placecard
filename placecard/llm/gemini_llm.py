"""Google Gemini backend via the REST generateContent API (stdlib only, no SDK needed).

POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
Header x-goog-api-key. Tools are sent as functionDeclarations with parametersJsonSchema.
Gemini 3 models attach a thoughtSignature to function-call parts; we replay the model's
content verbatim on the next request so the signatures are preserved.
Default model gemini-3.5-flash (free tier available in Malaysia; override with GEMINI_MODEL).
"""
import json
import time
import urllib.error
import urllib.request

from .base import LLMError, ToolCall, Turn

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class GeminiLLM:
    provider = "gemini"

    def __init__(self, api_key, model="gemini-3.5-flash", opener=urllib.request.urlopen, sleep=time.sleep,
                 max_output_tokens=8192):
        self.key, self.model, self.opener, self.sleep = api_key, model, opener, sleep
        self.max_output_tokens = max_output_tokens
        self.label = f"Gemini ({model})"

    @staticmethod
    def tools(specs):
        return [{"functionDeclarations": [{"name": s["name"], "description": s["description"],
                                           "parametersJsonSchema": s["parameters"]} for s in specs]}]

    @staticmethod
    def convert(messages):
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                raw = m.get("raw")
                if raw and raw.get("parts"):
                    out.append({"role": "model", "parts": raw["parts"]})
                else:
                    parts = [{"text": m["text"]}] if m.get("text") else []
                    parts += [{"functionCall": {"name": c.name, "args": c.args}} for c in m.get("tool_calls", [])]
                    out.append({"role": "model", "parts": parts or [{"text": "..."}]})
            elif m["role"] == "tool":
                parts = []
                for r in m["results"]:
                    try:
                        payload = json.loads(r["content"])
                    except (TypeError, ValueError):
                        payload = r["content"]
                    fr = {"name": r["name"], "response": {"error" if r.get("is_error") else "result": payload}}
                    if r["id"] and not r["id"].startswith("gemini-call-"):
                        fr["id"] = r["id"]
                    parts.append({"functionResponse": fr})
                out.append({"role": "user", "parts": parts})
        return out

    def _post(self, body):
        req = urllib.request.Request(ENDPOINT.format(model=self.model), data=json.dumps(body).encode(),
                                     headers={"x-goog-api-key": self.key, "Content-Type": "application/json"},
                                     method="POST")
        backoff = (2.0, 5.0, 10.0)  # free-tier models often return 503 "high demand" for a few seconds
        for attempt in range(len(backoff) + 1):
            last = attempt == len(backoff)
            try:
                with self.opener(req, timeout=90) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                msg = _message(e)
                if e.code in (429, 500, 503) and not last:
                    self.sleep(backoff[attempt])
                    continue
                if e.code == 429:
                    raise LLMError("Gemini rate limit or free-tier quota reached (429). Wait a minute, or switch "
                                   "GEMINI_MODEL to a Flash-Lite model.", retryable=True) from None
                if e.code in (401, 403):
                    raise LLMError(f"Gemini rejected the API key ({e.code}). Check GEMINI_API_KEY.", fatal=True) from None
                if e.code == 404:
                    raise LLMError(f"Unknown Gemini model '{self.model}'. Check GEMINI_MODEL.", fatal=True) from None
                raise LLMError(f"Gemini error ({e.code}): {msg}", retryable=e.code >= 500) from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if not last:
                    self.sleep(1.0)
                    continue
                raise LLMError(f"Could not reach Gemini: {getattr(e, 'reason', e)}", retryable=True) from None

    def chat(self, system, messages, tool_specs):
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": self.convert(messages),
            "tools": self.tools(tool_specs),
            "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
            "generationConfig": {"maxOutputTokens": self.max_output_tokens},
        }
        data = self._post(body)
        cands = data.get("candidates") or []
        if not cands:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMError(f"Gemini returned no answer ({reason}).")
        cand = cands[0]
        content = cand.get("content") or {"role": "model", "parts": []}
        text, calls = [], []
        for i, p in enumerate(content.get("parts") or []):
            if "functionCall" in p:
                fc = p["functionCall"]
                calls.append(ToolCall(fc.get("id") or f"gemini-call-{len(messages)}-{i}", fc.get("name", ""),
                                      dict(fc.get("args") or {})))
            elif p.get("text") and not p.get("thought"):
                text.append(p["text"])
        um = data.get("usageMetadata") or {}
        return Turn(text="\n".join(text).strip(), tool_calls=calls, raw=content,
                    input_tokens=um.get("promptTokenCount", 0) or 0,
                    output_tokens=(um.get("candidatesTokenCount", 0) or 0) + (um.get("thoughtsTokenCount", 0) or 0),
                    stop=cand.get("finishReason", ""))


def _message(e):
    try:
        return json.loads(e.read().decode()).get("error", {}).get("message", "")[:300]
    except Exception:
        return ""
