"""The alert message format: parsing, validation and action names.

Example:

    {
      "strategy_id": "BTC_STRATEGY_01",
      "signal_id": "{{strategy.order.id}}-{{timenow}}",
      "symbol": "{{ticker}}",
      "action": "BUY",
      "order_type": "MARKET",
      "quantity": 0.10,
      "price": {{close}},
      "stop_loss": 95000,
      "take_profit": 97000,
      "timestamp": "{{timenow}}",
      "timeframe": "15m"
    }
"""

import hashlib
import math
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

STANDARD_ACTIONS = (
    "BUY",
    "SELL",
    "CLOSE",
    "CLOSE_LONG",
    "CLOSE_SHORT",
    "CLOSE_ALL",
    "MODIFY_SL",
    "MODIFY_TP",
    "CANCEL_ORDER",
)
# Starting aliases for a new strategy; the user can change or remove them.
DEFAULT_ALIASES = {"LONG": "BUY", "SHORT": "SELL", "EXIT_LONG": "CLOSE_LONG", "EXIT_SHORT": "CLOSE_SHORT"}

MAX_BODY_BYTES = 8192


class SignalError(ValueError):
    """The alert cannot be used. The message says why, for the signal log."""


def normalise_symbol(raw: str) -> str:
    """'NSE:RELIANCE' -> 'RELIANCE'. The exchange prefix TradingView adds is dropped."""
    symbol = raw.strip().upper()
    if ":" in symbol:
        symbol = symbol.split(":", 1)[1]
    return symbol


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("must be a number")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("must be a number") from exc
    if not number.is_finite():
        raise ValueError("must be a finite number")
    return number


def parse_timestamp(value: Any) -> datetime:
    """ISO 8601 ({{timenow}}) or a Unix time in seconds or milliseconds."""
    if isinstance(value, bool):
        raise ValueError("timestamp is not a time")
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip().isdigit()):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("timestamp is not a time")
        if number > 1e12:  # milliseconds
            number /= 1000
        return datetime.fromtimestamp(number, UTC)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError("timestamp must be ISO 8601 (use {{timenow}}) or Unix time") from exc
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include a time zone, e.g. 2026-11-13T09:41:00Z")
        return parsed.astimezone(UTC)
    raise ValueError("timestamp is required")


class SignalPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    strategy_id: str | None = Field(default=None, max_length=60)
    signal_id: str | None = Field(default=None, max_length=100)
    symbol: str | None = Field(default=None, max_length=60)
    action: str = Field(max_length=30)
    order_type: Literal["MARKET", "LIMIT", "STOP", "STOP_LIMIT"] = "MARKET"
    quantity: Decimal | None = None
    price: Decimal | None = None
    trigger_price: Decimal | None = None
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    timestamp: datetime
    timeframe: str | None = Field(default=None, max_length=20)

    @field_validator("action", mode="before")
    @classmethod
    def _upper_action(cls, value: Any) -> Any:
        return value.strip().upper().replace(" ", "_") if isinstance(value, str) else value

    @field_validator("order_type", mode="before")
    @classmethod
    def _upper_type(cls, value: Any) -> Any:
        return value.strip().upper().replace("-", "_") if isinstance(value, str) else value

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str | None) -> str | None:
        if value is None:
            return None
        symbol = normalise_symbol(value)
        if not symbol:
            raise ValueError("symbol is empty")
        return symbol

    @field_validator("quantity", "price", "trigger_price", "stop_loss", "take_profit", mode="before")
    @classmethod
    def _number(cls, value: Any) -> Any:
        return _decimal(value)

    @field_validator("quantity", "price", "trigger_price", "stop_loss", "take_profit")
    @classmethod
    def _positive(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value <= 0:
            raise ValueError("must be greater than zero")
        return value

    @field_validator("timestamp", mode="before")
    @classmethod
    def _timestamp(cls, value: Any) -> datetime:
        if value is None or value == "":
            raise ValueError("timestamp is required (use {{timenow}})")
        return parse_timestamp(value)

    @model_validator(mode="after")
    def _consistent(self) -> "SignalPayload":
        if self.order_type in ("LIMIT", "STOP_LIMIT") and self.price is None:
            raise ValueError(f"{self.order_type} orders need a price")
        if self.order_type in ("STOP", "STOP_LIMIT") and self.trigger_price is None:
            raise ValueError(f"{self.order_type} orders need a trigger_price")
        return self


def resolve_action(action: str, aliases: dict[str, str]) -> str:
    resolved = aliases.get(action, action)
    if resolved not in STANDARD_ACTIONS:
        names = ", ".join([*STANDARD_ACTIONS, *sorted(aliases)])
        raise SignalError(f"unknown action '{action}'; use one of {names}")
    return resolved


def check_action_fields(action: str, payload: SignalPayload) -> None:
    if action != "CLOSE_ALL" and not payload.symbol:
        raise SignalError(f"{action} needs a symbol")
    if action == "MODIFY_SL" and payload.stop_loss is None:
        raise SignalError("MODIFY_SL needs stop_loss")
    if action == "MODIFY_TP" and payload.take_profit is None:
        raise SignalError("MODIFY_TP needs take_profit")
    if action in ("BUY", "SELL") and payload.stop_loss is not None and payload.price is not None:
        wrong_side = payload.stop_loss >= payload.price if action == "BUY" else payload.stop_loss <= payload.price
        if wrong_side:
            raise SignalError(f"stop_loss is on the wrong side of the price for a {action}")


def parse(body: bytes) -> tuple[dict, SignalPayload]:
    """Return (the raw JSON object, the validated payload) or raise SignalError."""
    import json

    if len(body) > MAX_BODY_BYTES:
        raise SignalError(f"alert message is larger than {MAX_BODY_BYTES} bytes")
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SignalError("alert message is not valid JSON") from exc
    if not isinstance(data, dict):
        raise SignalError("alert message must be a JSON object")
    try:
        return data, SignalPayload.model_validate(data)
    except ValidationError as exc:
        problems = []
        for error in exc.errors():
            field = ".".join(str(part) for part in error["loc"]) or "message"
            text = error["msg"].removeprefix("Value error, ")
            problems.append(f"{field}: {text}")
        raise SignalError("; ".join(problems)) from exc


def derived_signal_id(body: bytes) -> str:
    """Identity for an alert that carries no signal_id: the hash of its exact bytes.

    The message includes its timestamp, so two different alerts differ, while
    a resend of the same alert is recognised as a duplicate.
    """
    return "sha256:" + hashlib.sha256(body).hexdigest()[:40]


def sanitise_payload(data: dict) -> dict:
    """The stored copy of the alert, without anything secret."""
    return {key: value for key, value in data.items() if key.lower() not in ("passphrase", "secret", "token")}
