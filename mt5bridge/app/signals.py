"""Parsing of the JSON message a TradingView alert sends."""
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator


class Signal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal["buy", "sell", "close"]
    symbol: str
    lots: float | None = None
    sl: float | None = None
    tp: float | None = None
    # Close opposite positions on the symbol before opening the new one.
    reverse: bool = False
    # Label of a single account to trade; default is every enabled account.
    account: str | None = None
    comment: str = ""

    @field_validator("action", mode="before")
    @classmethod
    def _lower(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        v = v.strip()
        # TradingView's {{ticker}} can arrive as "OANDA:XAUUSD".
        if ":" in v:
            v = v.split(":", 1)[1]
        if not v or len(v) > 32:
            raise ValueError("symbol is empty or too long")
        return v

    @field_validator("lots", "sl", "tp")
    @classmethod
    def _positive(cls, v):
        if v is not None and (not math.isfinite(v) or v <= 0):
            raise ValueError("must be a positive number")
        return v

    @field_validator("comment")
    @classmethod
    def _comment(cls, v: str) -> str:
        return v[:31]  # MT5 order comments are limited to 31 characters

    @model_validator(mode="after")
    def _lots_required(self):
        if self.action in ("buy", "sell") and self.lots is None:
            raise ValueError("lots is required for buy and sell")
        return self


def parse_signal(body: bytes) -> Signal:
    """Raise ValueError with a user-readable message when the alert is unusable."""
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("alert message is not valid JSON")
    if not isinstance(data, dict):
        raise ValueError("alert message must be a JSON object")
    try:
        return Signal.model_validate(data)
    except ValidationError as exc:
        parts = []
        for err in exc.errors():
            field = ".".join(str(p) for p in err["loc"]) or "alert"
            parts.append(f"{field}: {err['msg']}")
        raise ValueError("; ".join(parts))
