"""Order size and price checks against an instrument's trading rules.

A size the user gave (in the alert, or as a fixed size) must already be valid:
it is rejected, never rounded. A size the platform calculates (percent of
capital, risk per trade) is rounded down to the instrument's step as part of
the calculation, and rejected if that leaves less than the minimum.
"""

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


class SizingError(ValueError):
    pass


@dataclass(frozen=True)
class Instrument:
    alert_symbol: str
    broker_symbol: str
    exchange: str | None = None
    product: str | None = None
    lot_size: Decimal = Decimal(1)
    quantity_step: Decimal | None = None
    min_quantity: Decimal | None = None
    max_quantity: Decimal | None = None
    tick_size: Decimal | None = None
    contract_size: Decimal = Decimal(1)
    mapped: bool = True

    @property
    def step(self) -> Decimal:
        return self.quantity_step or self.lot_size or Decimal(1)


def _is_multiple(value: Decimal, step: Decimal) -> bool:
    return step > 0 and (value / step) == (value / step).to_integral_value()


def floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def validate_quantity(quantity: Decimal, instrument: Instrument) -> Decimal:
    if quantity <= 0:
        raise SizingError("quantity must be greater than zero")
    step = instrument.step
    if not _is_multiple(quantity, step):
        raise SizingError(
            f"quantity {_fmt(quantity)} is not a multiple of the step {_fmt(step)} for {instrument.broker_symbol}"
        )
    if instrument.min_quantity is not None and quantity < instrument.min_quantity:
        raise SizingError(f"quantity {_fmt(quantity)} is below the minimum {_fmt(instrument.min_quantity)}")
    if instrument.max_quantity is not None and quantity > instrument.max_quantity:
        raise SizingError(f"quantity {_fmt(quantity)} is above the instrument maximum {_fmt(instrument.max_quantity)}")
    return quantity


def validate_prices(instrument: Instrument, **prices: Decimal | None) -> None:
    tick = instrument.tick_size
    if not tick:
        return
    for name, value in prices.items():
        if value is not None and not _is_multiple(value, tick):
            raise SizingError(f"{name} {_fmt(value)} is not a multiple of the tick size {_fmt(tick)}")


def compute_quantity(
    mode: str,
    value: Decimal | None,
    *,
    signal_quantity: Decimal | None,
    instrument: Instrument,
    capital: Decimal | None,
    price: Decimal | None,
    stop_loss: Decimal | None,
) -> Decimal:
    if mode == "signal":
        if signal_quantity is None:
            raise SizingError("the alert has no quantity and this account sizes orders from the alert")
        return validate_quantity(signal_quantity, instrument)
    if mode == "fixed":
        if value is None:
            raise SizingError("fixed sizing has no size set")
        return validate_quantity(value, instrument)

    if value is None or value <= 0:
        raise SizingError(f"{mode} sizing has no value set")
    if price is None:
        raise SizingError(f'{mode} sizing needs a price in the alert (add "price": {{{{close}}}})')
    if mode == "percent_capital":
        if capital is None or capital <= 0:
            raise SizingError("percent-of-capital sizing needs the account's capital, which is not known yet")
        raw = capital * value / Decimal(100) / (price * instrument.contract_size)
    elif mode == "risk_per_trade":
        if stop_loss is None:
            raise SizingError("risk-per-trade sizing needs a stop_loss in the alert")
        per_unit = abs(price - stop_loss) * instrument.contract_size
        if per_unit <= 0:
            raise SizingError("stop_loss equals the price, so the risk per unit is zero")
        raw = value / per_unit
    else:
        raise SizingError(f"unknown sizing mode {mode}")
    quantity = floor_to_step(raw, instrument.step)
    if quantity <= 0 or (instrument.min_quantity is not None and quantity < instrument.min_quantity):
        raise SizingError(
            f"the calculated size {_fmt(raw.quantize(Decimal('0.0001')))} is below the smallest tradable size "
            f"{_fmt(instrument.min_quantity or instrument.step)}"
        )
    if instrument.max_quantity is not None and quantity > instrument.max_quantity:
        raise SizingError(f"the calculated size {_fmt(quantity)} is above the instrument maximum")
    return quantity


def _fmt(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text
