"""Parsing of the JSON message a TradingView alert sends."""
import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator


KITE_EXCHANGES = {"NSE", "BSE", "NFO", "BFO", "MCX", "CDS", "BCD"}


class Signal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: Literal["buy", "sell", "close"]
    symbol: str
    # Size: `lots` for MT5 accounts, `quantity` for Zerodha accounts.
    lots: float | None = None
    quantity: int | None = None
    # Zerodha only.
    exchange: str | None = None
    product: Literal["MIS", "CNC", "NRML"] = "MIS"
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    price: float | None = None
    # Restrict the alert to one kind of account.
    broker: Literal["mt5", "kite"] | None = None
    sl: float | None = None
    tp: float | None = None
    # Close opposite positions on the symbol before opening the new one.
    reverse: bool = False
    # Label of a single account to trade; default is every enabled account.
    account: str | None = None
    comment: str = ""

    @model_validator(mode="before")
    @classmethod
    def _exchange_from_prefix(cls, data):
        # TradingView's {{ticker}} can arrive as "NSE:RELIANCE": use the prefix
        # as the Zerodha exchange unless the alert names one.
        if isinstance(data, dict) and not data.get("exchange"):
            symbol = data.get("symbol")
            if isinstance(symbol, str) and ":" in symbol:
                prefix = symbol.split(":", 1)[0].strip().upper()
                if prefix in KITE_EXCHANGES:
                    data = {**data, "exchange": prefix}
        return data

    @field_validator("action", "broker", mode="before")
    @classmethod
    def _lower(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("exchange", "product", "order_type", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("exchange")
    @classmethod
    def _exchange(cls, v):
        if v is not None and v not in KITE_EXCHANGES:
            raise ValueError("must be one of " + ", ".join(sorted(KITE_EXCHANGES)))
        return v

    @field_validator("quantity")
    @classmethod
    def _quantity(cls, v):
        if v is not None and v <= 0:
            raise ValueError("must be a positive whole number")
        return v

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, v: str) -> str:
        v = v.strip()
        if ":" in v:
            v = v.split(":", 1)[1]
        if not v or len(v) > 32:
            raise ValueError("symbol is empty or too long")
        return v

    @field_validator("lots", "sl", "tp", "price")
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
        if self.action in ("buy", "sell") and self.lots is None and self.quantity is None:
            raise ValueError("lots (MT5) or quantity (Zerodha) is required for buy and sell")
        if self.order_type == "LIMIT" and self.price is None:
            raise ValueError("price is required for a LIMIT order")
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
