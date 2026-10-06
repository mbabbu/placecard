"""Provider-neutral types for the agent loop.

Conversation history is a list of plain dicts, converted by each provider:
  {"role": "user", "content": str}
  {"role": "assistant", "text": str, "tool_calls": [ToolCall], "raw": <provider content to replay verbatim>}
  {"role": "tool", "results": [{"id", "name", "content": str, "is_error": bool}]}
"""
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    raw: object = None
    input_tokens: int = 0
    output_tokens: int = 0
    stop: str = ""


class LLMError(Exception):
    def __init__(self, message, retryable=False, fatal=False):
        super().__init__(message)
        self.retryable, self.fatal = retryable, fatal
