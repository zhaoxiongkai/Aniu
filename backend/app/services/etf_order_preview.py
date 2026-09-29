"""Read-only preview for previously accepted Shanghai ETFs."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from app.services.order_gateway import SH_ETF_CODES, _verified_market_quote

SHANGHAI = ZoneInfo("Asia/Shanghai")
ETF_TICK = Decimal("0.001")


def preview_etf_order(
    client: Any, *, symbol: str, quantity: int, limit_price: int | float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Show a fresh, exact-tick proposal without authorizing a vendor write."""
    bare_symbol = str(symbol).split(".")[0]
    if bare_symbol not in SH_ETF_CODES or symbol not in {bare_symbol, f"{bare_symbol}.SH"}:
        raise RuntimeError("ETF code has no verified MX order history in this account.")
    if type(quantity) is not int or quantity <= 0 or quantity % 100:
        raise RuntimeError("ETF preview quantity must be a positive multiple of 100 units.")
    now = now or datetime.now(SHANGHAI)
    if now.tzinfo is None:
        raise RuntimeError("ETF preview time must include a timezone.")
    canonical_symbol = f"{bare_symbol}.SH"
    quote = _verified_market_quote(client, canonical_symbol, now, asset_type="etf")
    if isinstance(limit_price, bool) or (limit_price is not None and type(limit_price) not in {int, float}):
        raise RuntimeError("ETF limit price must be a positive numeric value.")
    try:
        price = Decimal(str(quote["price"] if limit_price is None else limit_price))
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError("ETF limit price is malformed.") from exc
    if not price.is_finite() or price <= 0 or price % ETF_TICK:
        raise RuntimeError("ETF limit price must be positive with 0.001-yuan precision.")
    return {
        "symbol": canonical_symbol, "asset_type": "ETF", "quantity": quantity,
        "quantity_unit": "份", "price_type": "LIMIT",
        "limit_price_yuan": f"{price:.3f}",
        "max_notional_yuan": f"{quantity * price:.3f}",
        "market_quote": quote,
        "can_submit": False,
        "block_reason": "This read-only preview cannot submit an order or authorize trading.",
    }
