"""Currencies: converting and showing amounts in the site's currency.

Pure functions (no Flask, no database). Every piece keeps the currency its
price paid and estimated value were entered in (items.currency); the
Settings page chooses the currency the site shows amounts in and the exchange
rate between the two. A conversion happens only for display, so changing the
rate later never alters a saved amount.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

CURRENCIES: dict[str, str] = {"USD": "US dollars", "CAD": "Canadian dollars"}
DEFAULT_CURRENCY = "USD"  # every amount saved before currencies existed
SYMBOLS = {"USD": "US$", "CAD": "CA$"}
CENT = Decimal("0.01")
MIN_RATE = Decimal("0.01")
MAX_RATE = Decimal("100")


@dataclass(frozen=True)
class MoneySettings:
    """The site currency and the rate: how many Canadian dollars one US dollar buys
    (None until it has been entered)."""

    currency: str = DEFAULT_CURRENCY
    usd_cad_rate: Decimal | None = None
    rate_set_on: dt.date | None = None

    @classmethod
    def from_rows(cls, values: dict[str, str]) -> "MoneySettings":
        """From the site_settings table's name -> value pairs; bad or missing values fall back."""
        currency = values.get("currency") if values.get("currency") in CURRENCIES else DEFAULT_CURRENCY
        rate = parse_rate(values.get("usd_cad_rate"))
        try:
            set_on = dt.date.fromisoformat(values["usd_cad_rate_set_on"]) if values.get("usd_cad_rate_set_on") else None
        except ValueError:
            set_on = None
        return cls(currency=currency, usd_cad_rate=rate, rate_set_on=set_on if rate else None)


def parse_rate(raw: Any) -> Decimal | None:
    """A positive exchange rate with at most 6 decimal places, or None."""
    try:
        rate = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not rate.is_finite() or not MIN_RATE <= rate <= MAX_RATE or rate.as_tuple().exponent < -6:  # type: ignore[operator]
        return None
    return rate


def convert(amount: Any, from_currency: str | None, to_currency: str, rate: Decimal | None) -> Decimal | None:
    """The amount in to_currency, to the cent; None if it can't be converted (no rate)."""
    if amount is None or amount == "":
        return None
    value = Decimal(amount)
    source = from_currency or DEFAULT_CURRENCY
    if source == to_currency:
        return value.quantize(CENT, rounding=ROUND_HALF_UP)
    if rate is None:
        return None
    converted = value * rate if source == "USD" else value / rate
    return converted.quantize(CENT, rounding=ROUND_HALF_UP)


def symbol(currency: str | None, site_currency: str) -> str:
    """ "$" for the site's own currency while it is US dollars (as the site always showed);
    otherwise the currency is spelled out: "CA$", "US$"."""
    currency = currency or DEFAULT_CURRENCY
    if currency == site_currency == "USD":
        return "$"
    return SYMBOLS.get(currency, currency + " ")


def fmt(amount: Any, currency: str | None, site_currency: str) -> str:
    """ "$1,234.50", "CA$62.10"; "" for no amount."""
    if amount is None or amount == "":
        return ""
    return f"{symbol(currency, site_currency)}{Decimal(amount):,.2f}"


def show(amount: Any, currency: str | None, settings: MoneySettings) -> dict[str, str]:
    """How one saved amount is shown: {"text": in the site currency, "original": what was
    entered, when that differs}. Without a rate, an amount in the other currency is shown as
    entered."""
    if amount is None or amount == "":
        return {"text": "", "original": ""}
    source = currency or DEFAULT_CURRENCY
    converted = convert(amount, source, settings.currency, settings.usd_cad_rate)
    if converted is None:
        return {"text": fmt(amount, source, settings.currency), "original": ""}
    original = fmt(amount, source, settings.currency) if source != settings.currency else ""
    return {"text": fmt(converted, settings.currency, settings.currency), "original": original}


def total(rows: list[dict[str, Any]], field: str, settings: MoneySettings) -> dict[str, Any]:
    """Add up amounts saved in different currencies. rows: [{"currency", field}, ...].

    Returns {"amount": the sum in the site currency, "unconverted": {currency: sum}}:
    amounts that need a rate when there isn't one are kept apart instead of guessed.
    """
    amount = Decimal("0.00")
    unconverted: dict[str, Decimal] = {}
    for row in rows:
        value = row.get(field)
        if value is None:
            continue
        converted = convert(value, row.get("currency"), settings.currency, settings.usd_cad_rate)
        if converted is None:
            key = row.get("currency") or DEFAULT_CURRENCY
            unconverted[key] = unconverted.get(key, Decimal("0.00")) + Decimal(value)
        else:
            amount += converted
    return {"amount": amount, "unconverted": unconverted}


def retail(srp_usd: Any, srp_cad: Any, settings: MoneySettings) -> str:
    """A catalog entry's original retail price in the site currency as published (never
    converted); the other currency, labelled, when the catalog has none in this one."""
    own, other = (srp_cad, srp_usd) if settings.currency == "CAD" else (srp_usd, srp_cad)
    other_currency = "USD" if settings.currency == "CAD" else "CAD"
    if own is not None and own != "":
        return fmt(own, settings.currency, settings.currency)
    if other is not None and other != "":
        return fmt(other, other_currency, settings.currency)
    return ""
