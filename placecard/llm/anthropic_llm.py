"""Anthropic Claude backend (official `anthropic` Python SDK, Messages API with client tools).

Default model claude-haiku-4-5 (cheapest current Claude; override with ANTHROPIC_MODEL).
The key is read by config.py from ANTHROPIC_API_KEY; it is never logged or sent elsewhere.
"""
from .base import LLMError, ToolCall, Turn


class AnthropicLLM:
    provider = "anthropic"

    def __init__(self, api_key=None, model="claude-haiku-4-5", client=None, max_tokens=8192):
        if client is None:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover - depends on install
                raise LLMError("The 'anthropic' package is not installed (pip install -r requirements.txt).",
                               fatal=True) from e
            client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=60.0)
        self.client, self.model, self.max_tokens = client, model, max_tokens
        self.label = f"Claude ({model})"

    @staticmethod
    def tools(specs):
        return [{"name": s["name"], "description": s["description"], "input_schema": s["parameters"]} for s in specs]

    @staticmethod
    def convert(messages):
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                content = m.get("raw")
                if not content:
                    content = []
                    if m.get("text"):
                        content.append({"type": "text", "text": m["text"]})
                    for c in m.get("tool_calls", []):
                        content.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.args})
                out.append({"role": "assistant", "content": content or [{"type": "text", "text": "(thinking)"}]})
            elif m["role"] == "tool":
                out.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"],
                     **({"is_error": True} if r.get("is_error") else {})} for r in m["results"]]})
        return out

    def chat(self, system, messages, tool_specs):
        try:
            import anthropic
            errors = anthropic
        except ImportError:  # tests inject a fake client
            errors = None
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                tools=self.tools(tool_specs),
                messages=self.convert(messages),
                cache_control={"type": "ephemeral"},
            )
        except Exception as e:
            raise _map_error(e, errors) from None
        text, calls = [], []
        for b in resp.content:
            if b.type == "text":
                text.append(b.text)
            elif b.type == "tool_use":
                calls.append(ToolCall(b.id, b.name, dict(b.input or {})))
        usage = getattr(resp, "usage", None)
        return Turn(text="\n".join(t for t in text if t).strip(), tool_calls=calls, raw=resp.content,
                    input_tokens=getattr(usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(usage, "output_tokens", 0) or 0,
                    stop=getattr(resp, "stop_reason", "") or "")


def _map_error(e, sdk):
    if sdk is None:
        return LLMError(f"Claude request failed: {e}")
    if isinstance(e, sdk.AuthenticationError):
        return LLMError("Anthropic rejected the API key (401). Check ANTHROPIC_API_KEY.", fatal=True)
    if isinstance(e, sdk.PermissionDeniedError):
        return LLMError("The Anthropic key lacks permission for this model (403).", fatal=True)
    if isinstance(e, sdk.NotFoundError):
        return LLMError("Unknown Claude model. Check ANTHROPIC_MODEL.", fatal=True)
    if isinstance(e, sdk.RateLimitError):
        return LLMError("Claude rate limit reached (429). Try again in a minute.", retryable=True)
    if isinstance(e, sdk.BadRequestError):
        return LLMError(f"Claude rejected the request (400): {getattr(e, 'message', e)}")
    if isinstance(e, sdk.APIStatusError):
        return LLMError(f"Claude API error ({e.status_code}). Try again shortly.", retryable=e.status_code >= 500)
    if isinstance(e, sdk.APIConnectionError):
        return LLMError("Could not reach the Claude API.", retryable=True)
    return LLMError(f"Claude request failed: {type(e).__name__}")
