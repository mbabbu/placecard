"""LLM provider abstraction: Anthropic Claude, Google Gemini, or a keyless MOCK agent."""
from .. import config
from .base import LLMError, ToolCall, Turn  # noqa: F401


def get_llm(provider=None):
    provider = provider or config.llm_provider()
    if provider == "anthropic":
        if not config.anthropic_key():
            raise LLMError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set.", fatal=True)
        from .anthropic_llm import AnthropicLLM
        return AnthropicLLM(config.anthropic_key(), config.anthropic_model())
    if provider == "gemini":
        if not config.gemini_key():
            raise LLMError("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set.", fatal=True)
        from .gemini_llm import GeminiLLM
        return GeminiLLM(config.gemini_key(), config.gemini_model())
    from .mock_llm import DemoAgentLLM
    return DemoAgentLLM(delay=config.int_env("MOCK_DELAY_MS", 450) / 1000)
