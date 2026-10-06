"""Configuration. Every secret is read from the environment here and nowhere else.

Never put keys in the repo. Set them in your shell or in the host's secret settings:

    QLOO_API_KEY       hackathon key (only works against https://hackathon.api.qloo.com)
    LLM_PROVIDER       auto | mock | gemini | anthropic   (default: auto)
    GEMINI_API_KEY     Google AI Studio key            (provider "gemini")
    GEMINI_MODEL       default gemini-3.5-flash
    ANTHROPIC_API_KEY  Anthropic Console key           (provider "anthropic")
    ANTHROPIC_MODEL    default claude-haiku-4-5
    QLOO_CACHE_FILE    optional path of a JSON file that keeps Qloo results across restarts (saves quota)
    QLOO_RPS           requests per second sent to Qloo (default 4; the hackathon key allows 5)
    OWN_TAG_WEIGHT     weight of the favourites' own Qloo tags next to the taste-analysis tags (default 2.0; 0 = off)

"auto" picks anthropic if ANTHROPIC_API_KEY is set, else gemini if GEMINI_API_KEY is set,
else the keyless scripted MOCK agent. Without QLOO_API_KEY the Qloo side runs on clearly
labelled SAMPLE data.
"""
import os

QLOO_BASE_URL = os.environ.get("QLOO_BASE_URL", "https://hackathon.api.qloo.com")
DEFAULT_GEMINI_MODEL = "gemini-3.5-flash"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-4-5"


def _env(name):
    return os.environ.get(name, "").strip() or None


def qloo_key():
    return _env("QLOO_API_KEY")


def qloo_mode():
    return "live" if qloo_key() else "sample"


def qloo_cache_file():
    return _env("QLOO_CACHE_FILE")


def float_env(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def gemini_key():
    return _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")


def gemini_model():
    return _env("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL


def anthropic_key():
    return _env("ANTHROPIC_API_KEY")


def anthropic_model():
    return _env("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL


def llm_provider():
    """Resolve which LLM backend to use: 'anthropic', 'gemini' or 'mock'."""
    want = (_env("LLM_PROVIDER") or "auto").lower()
    if want in ("mock", "gemini", "anthropic"):
        return want
    if anthropic_key():
        return "anthropic"
    if gemini_key():
        return "gemini"
    return "mock"


def int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Abuse limits for a public demo that spends the owner's LLM and Qloo quota.
# The hackathon key allows only 10,000 Qloo requests a month and a cold 40-guest run costs about 145, so the defaults
# are modest; repeated favourites (and the preset lists) are served from the cache and cost nothing.
RUNS_PER_IP_PER_HOUR = int_env("RUNS_PER_IP_PER_HOUR", 8)
RUNS_PER_DAY = int_env("RUNS_PER_DAY", 120)
MAX_AGENT_TURNS = int_env("MAX_AGENT_TURNS", 16)
# ~4 Qloo requests per guest (up to 4 favourite searches + 1 taste analysis; repeated favourites are cached).
MAX_QLOO_CALLS = int_env("MAX_QLOO_CALLS", 600)
MAX_GUESTS = int_env("MAX_GUESTS", 120)
# The hackathon key allows 5 requests per second and 10,000 per month (X-*-RateLimit headers). Stay under the first,
# and stop spending before the second runs out so a public demo cannot exhaust it in one burst.
QLOO_RPS = float_env("QLOO_RPS", 4.0)
QLOO_MONTH_RESERVE = int_env("QLOO_MONTH_RESERVE", 100)
OWN_TAG_WEIGHT = float_env("OWN_TAG_WEIGHT", 2.0)
