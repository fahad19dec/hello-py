"""Providers, models and analysts offered by the UI.

Everything is read from the installed ``tradingagents`` package when it is
importable, so the dropdowns never drift from the framework. The bundled
fallbacks below keep the Settings page usable *before* the framework is
installed — the app is meant to be openable first and configured second.
"""

from __future__ import annotations

import functools
import importlib
import os

ModelOption = tuple[str, str]

#: Analyst teams, in the order the graph runs them.
ANALYSTS: list[tuple[str, str, str]] = [
    ("market", "Market Analyst", "Price action and technical indicators"),
    ("social", "Sentiment Analyst", "Social and retail sentiment"),
    ("news", "News Analyst", "Company and macro headlines"),
    ("fundamentals", "Fundamentals Analyst", "Financial statements and insider activity"),
]

ASSET_TYPES: list[tuple[str, str]] = [("stock", "Stock"), ("crypto", "Crypto")]

#: (display name, provider key, default base URL) — mirrors cli.utils.
_FALLBACK_PROVIDERS: list[tuple[str, str, str | None]] = [
    ("OpenAI", "openai", "https://api.openai.com/v1"),
    ("Google", "google", None),
    ("Anthropic", "anthropic", "https://api.anthropic.com/"),
    ("xAI", "xai", "https://api.x.ai/v1"),
    ("DeepSeek", "deepseek", "https://api.deepseek.com"),
    ("Qwen", "qwen", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    ("GLM", "glm", "https://open.bigmodel.cn/api/paas/v4/"),
    ("MiniMax", "minimax", "https://api.minimax.io/v1"),
    ("OpenRouter", "openrouter", "https://openrouter.ai/api/v1"),
    ("Mistral", "mistral", "https://api.mistral.ai/v1"),
    ("Kimi (Moonshot)", "kimi", "https://api.moonshot.ai/v1"),
    ("Groq", "groq", "https://api.groq.com/openai/v1"),
    ("NVIDIA NIM", "nvidia", "https://integrate.api.nvidia.com/v1"),
    ("Azure OpenAI", "azure", None),
    ("Amazon Bedrock", "bedrock", None),
    ("Ollama", "ollama", "http://localhost:11434/v1"),
    ("OpenAI-compatible endpoint", "openai_compatible", None),
]

_FALLBACK_KEY_ENV: dict[str, str | None] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "azure": "AZURE_OPENAI_API_KEY",
    "bedrock": None,
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
    "glm": "ZHIPU_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "kimi": "MOONSHOT_API_KEY",
    "groq": "GROQ_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "ollama": None,
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
}

_CUSTOM_ONLY: list[ModelOption] = [("Custom model ID", "custom")]

#: Small static catalog so the Settings page offers real choices before the
#: framework is installed. Once it is, the framework's own catalog wins.
_FALLBACK_MODELS: dict[str, dict[str, list[ModelOption]]] = {
    "openai": {
        "quick": [("GPT-5.6 Luna", "gpt-5.6-luna"), ("GPT-5.6 Terra", "gpt-5.6-terra")],
        "deep": [("GPT-5.6", "gpt-5.6"), ("GPT-5.5", "gpt-5.5")],
    },
    "anthropic": {
        "quick": [("Claude Sonnet 5", "claude-sonnet-5"), ("Claude Haiku 4.5", "claude-haiku-4-5")],
        "deep": [("Claude Fable 5", "claude-fable-5"), ("Claude Opus 4.8", "claude-opus-4-8")],
    },
    "google": {
        "quick": [("Gemini 3.5 Flash", "gemini-3.5-flash")],
        "deep": [("Gemini 3.1 Pro", "gemini-3.1-pro-preview")],
    },
    "deepseek": {
        "quick": [("DeepSeek V4 Flash", "deepseek-v4-flash")],
        "deep": [("DeepSeek V4 Pro", "deepseek-v4-pro")],
    },
    "xai": {
        "quick": [("Grok 4.3", "grok-4.3")],
        "deep": [("Grok 4.3", "grok-4.3")],
    },
}

#: Data-vendor keys that need their own API key on top of the LLM key.
DATA_KEY_ENV: list[tuple[str, str, str]] = [
    ("ALPHA_VANTAGE_API_KEY", "Alpha Vantage", "Market data / fundamentals / news vendor"),
    ("FRED_API_KEY", "FRED", "Macro indicators (required by the macro analyst tools)"),
    ("FINNHUB_API_KEY", "Finnhub", "Optional supplementary market data"),
]


def framework() -> tuple[object | None, str]:
    """Return the imported ``tradingagents`` module, or ``(None, reason)``."""
    try:
        module = importlib.import_module("tradingagents")
    except Exception as exc:  # pragma: no cover - depends on the environment
        return None, f"{type(exc).__name__}: {exc}"
    return module, getattr(module, "__version__", "")


def is_installed() -> bool:
    return framework()[0] is not None


def _try(dotted: str, attribute: str):
    """Import ``attribute`` from ``dotted``; return None when unavailable."""
    try:
        return getattr(importlib.import_module(dotted), attribute)
    except Exception:
        return None


@functools.lru_cache(maxsize=1)
def _provider_table() -> list[tuple[str, str, str | None]]:
    table = _try("cli.utils", "_llm_provider_table")
    if table is not None:
        try:
            return list(table())
        except Exception:
            pass
    return list(_FALLBACK_PROVIDERS)


def providers() -> list[tuple[str, str]]:
    """(display name, provider key) for every supported provider."""
    return [(name, key) for name, key, _ in _provider_table()]


def provider_label(provider: str) -> str:
    for name, key, _ in _provider_table():
        if key == provider:
            return name
    return provider


def default_backend_url(provider: str) -> str | None:
    """Default endpoint for a provider, honouring OLLAMA_BASE_URL."""
    if provider == "ollama":
        return os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
    for _, key, url in _provider_table():
        if key == provider:
            return url
    return None


def api_key_env(provider: str) -> str | None:
    """Env var holding this provider's API key, or None when it needs no key."""
    getter = _try("tradingagents.llm_clients.api_key_env", "get_api_key_env")
    if getter is not None:
        try:
            return getter(provider)
        except Exception:
            pass
    return _FALLBACK_KEY_ENV.get(provider)


def model_options(provider: str, mode: str) -> list[ModelOption]:
    """Model choices for ``mode`` ("quick" or "deep").

    Providers that serve many or fast-moving models (OpenRouter, Azure,
    Bedrock, ...) are not listed in the framework catalog at all; those get a
    free-text model ID instead of a stale dropdown.
    """
    getter = _try("tradingagents.llm_clients.model_catalog", "get_model_options")
    if getter is not None:
        try:
            return list(getter(provider, mode))
        except Exception:
            pass
    options = list(_FALLBACK_MODELS.get(provider, {}).get(mode, []))
    return options + list(_CUSTOM_ONLY)


def requires_custom_url(provider: str) -> bool:
    """True when the user must supply the endpoint themselves."""
    return provider in {"openai_compatible", "azure"}
