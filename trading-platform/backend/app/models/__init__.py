"""Importing this package registers every table on Base.metadata (Alembic relies on it)."""

from .audit import AuditLog
from .identity import AuthSession, EmailToken, RecoveryCode, User, UserCredential
from .trading import (
    BrokerAccount,
    BrokerCredential,
    Fill,
    InstrumentMapping,
    Order,
    OrderEvent,
    PlatformControl,
    Position,
    RiskLimits,
    Signal,
    Strategy,
    StrategyAccount,
    StrategyVersion,
    TradingControl,
    Webhook,
)

__all__ = [
    "AuditLog",
    "AuthSession",
    "BrokerAccount",
    "BrokerCredential",
    "EmailToken",
    "Fill",
    "InstrumentMapping",
    "Order",
    "OrderEvent",
    "PlatformControl",
    "Position",
    "RecoveryCode",
    "RiskLimits",
    "Signal",
    "Strategy",
    "StrategyAccount",
    "StrategyVersion",
    "TradingControl",
    "User",
    "UserCredential",
    "Webhook",
]
