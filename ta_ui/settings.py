"""User settings for the UI: persistence, secrets and config assembly.

Preferences (provider, models, rounds, vendors) are written to a JSON file so
they survive a restart. API keys are deliberately *not* persisted here — they
live in the process environment, a ``.env`` file or Streamlit secrets, and are
only pushed into ``os.environ`` right before a run.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import catalog

SETTINGS_PATH = Path(
    os.environ.get("TRADINGAGENTS_UI_SETTINGS")
    or Path.home() / ".tradingagents" / "ui_settings.json"
)

VENDOR_CHOICES: dict[str, list[str]] = {
    "core_stock_apis": ["yfinance", "alpha_vantage"],
    "technical_indicators": ["yfinance", "alpha_vantage"],
    "fundamental_data": ["yfinance", "alpha_vantage"],
    "news_data": ["yfinance", "alpha_vantage"],
}

LANGUAGES = [
    "English", "Chinese", "Japanese", "Korean", "French",
    "German", "Spanish", "Portuguese", "Hindi", "Arabic",
]


@dataclass
class Settings:
    """Everything the UI lets a user configure, with framework defaults."""

    llm_provider: str = "openai"
    deep_think_llm: str = "gpt-5.6"
    quick_think_llm: str = "gpt-5.6-luna"
    backend_url: str = ""
    analysts: list[str] = field(
        default_factory=lambda: ["market", "social", "news", "fundamentals"]
    )
    max_debate_rounds: int = 1
    max_risk_discuss_rounds: int = 1
    output_language: str = "English"
    checkpoint_enabled: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    llm_max_retries: int | None = None
    reasoning_effort: str = ""
    data_vendors: dict[str, str] = field(
        default_factory=lambda: {
            "core_stock_apis": "yfinance",
            "technical_indicators": "yfinance",
            "fundamental_data": "yfinance",
            "news_data": "yfinance",
        }
    )
    results_dir: str = ""
    save_reports: bool = True

    # ---------------------------------------------------------------- load/save
    @classmethod
    def load(cls) -> Settings:
        """Read saved preferences, ignoring anything unknown or corrupt."""
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            return cls()
        if not isinstance(raw, dict):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> Path:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return SETTINGS_PATH

    # ------------------------------------------------------------------ config
    def resolved_backend_url(self) -> str | None:
        return self.backend_url.strip() or catalog.default_backend_url(self.llm_provider)

    def resolved_results_dir(self) -> Path:
        """Where runs are written: this setting, else the framework's own default.

        The framework honours ``TRADINGAGENTS_RESULTS_DIR``, so reading its
        config keeps the history page pointed at the same place a run wrote to.
        """
        if self.results_dir.strip():
            return Path(self.results_dir.strip()).expanduser()
        try:
            from tradingagents.default_config import DEFAULT_CONFIG

            configured = DEFAULT_CONFIG.get("results_dir")
            if configured:
                return Path(configured).expanduser()
        except Exception:
            pass
        return Path(
            os.environ.get("TRADINGAGENTS_RESULTS_DIR")
            or Path.home() / ".tradingagents" / "logs"
        ).expanduser()

    def reports_dir(self) -> Path:
        return self.resolved_results_dir() / "reports"

    def to_config(self) -> dict:
        """Build the ``tradingagents`` config dict for a run."""
        try:
            from tradingagents.default_config import DEFAULT_CONFIG

            config = copy.deepcopy(dict(DEFAULT_CONFIG))
        except Exception:
            config = {}

        config.update(
            {
                "llm_provider": self.llm_provider,
                "deep_think_llm": self.deep_think_llm.strip(),
                "quick_think_llm": self.quick_think_llm.strip(),
                "backend_url": self.resolved_backend_url(),
                "output_language": self.output_language,
                "max_debate_rounds": int(self.max_debate_rounds),
                "max_risk_discuss_rounds": int(self.max_risk_discuss_rounds),
                "checkpoint_enabled": bool(self.checkpoint_enabled),
            }
        )
        if self.temperature is not None:
            config["temperature"] = float(self.temperature)
        if self.max_tokens:
            config["max_tokens"] = int(self.max_tokens)
        if self.llm_max_retries is not None:
            config["llm_max_retries"] = int(self.llm_max_retries)
        if self.results_dir.strip():
            config["results_dir"] = self.results_dir.strip()

        vendors = dict(config.get("data_vendors") or {})
        vendors.update(self.data_vendors)
        config["data_vendors"] = vendors

        # The reasoning/thinking knob is named per provider; set only the one
        # that applies so the others keep the provider's own default.
        effort = self.reasoning_effort.strip()
        if effort:
            key = {
                "openai": "openai_reasoning_effort",
                "anthropic": "anthropic_effort",
                "google": "google_thinking_level",
            }.get(self.llm_provider)
            if key:
                config[key] = effort
        return config


# ---------------------------------------------------------------------- secrets

def env_value(name: str) -> str:
    """Current value of an API-key env var, including Streamlit secrets."""
    value = os.environ.get(name, "")
    if value:
        return value
    try:  # st.secrets raises when no secrets file exists
        import streamlit as st

        return str(st.secrets[name])
    except Exception:
        return ""


def apply_secrets(secrets: dict[str, str]) -> None:
    """Push non-empty UI-entered keys into the process environment."""
    for name, value in secrets.items():
        if value and value.strip():
            os.environ[name] = value.strip()


def load_dotenv_if_present() -> bool:
    """Load a repo-local ``.env`` so keys can be set without the UI."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return False
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
        return True
    except Exception:
        # python-dotenv is a tradingagents dependency; parse the simple case
        # ourselves when it is missing so a .env still works standalone.
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            os.environ.setdefault(name.strip(), value.strip().strip("'\""))
        return True


def missing_keys(settings: Settings) -> list[str]:
    """Env vars the selected provider needs but that are not set."""
    name = catalog.api_key_env(settings.llm_provider)
    if not name:
        return []
    return [] if env_value(name) else [name]
