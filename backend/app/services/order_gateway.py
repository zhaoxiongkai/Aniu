"""Deterministic pre-order policy and durable intent reservation."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db.database import session_scope
from app.db.models import AppSettings, ManualOrderGrant, StrategyRun, TradeIntent
from app.services.trading_calendar_service import trading_calendar_service

logger = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")
# These Shanghai funds have prior MX rc=0/orderID responses in this account.
# This is a bounded capability list, not an arbitrary 5xxxxx code classifier.
SH_ETF_CODES = frozenset({"510050", "510300", "512100", "588000", "588170"})


def _verified_market_quote(
    client: Any, symbol: str, now: datetime, *, asset_type: str = "stock",
) -> dict[str, Any]:
    bare_symbol = symbol.split(".")[0]
    if asset_type == "etf":
        if bare_symbol not in SH_ETF_CODES:
            raise RuntimeError("ETF code has no verified MX order history in this account.")
        expected_suffix = "SH"
    elif asset_type == "stock":
        if not re.fullmatch("[036][0-9]{5}", bare_symbol):
            raise RuntimeError("Only six-digit Shanghai/Shenzhen A-share symbols are supported.")
        expected_suffix = "SH" if bare_symbol.startswith("6") else "SZ"
    else:
        raise RuntimeError("Unsupported market quote asset type.")
    canonical_symbol = f"{bare_symbol}.{expected_suffix}"
    if symbol not in {bare_symbol, canonical_symbol}:
        raise RuntimeError("Symbol exchange suffix does not match its code.")
    payload = client.query_market(f"{canonical_symbol} 最新价")
    if not isinstance(payload, dict) or str(payload.get("code")) not in {"0", "200"}:
        raise RuntimeError("Market data request did not succeed.")
    data = payload.get("data")
    if isinstance(data, dict) and "data" in data:
        if str(data.get("code")) not in {"0", "200"}:
            raise RuntimeError("Market data request did not succeed.")
        inner = data.get("data")
        search = inner.get("searchDataResultDTO") if isinstance(inner, dict) else None
        tables = search.get("dataTableDTOList") if isinstance(search, dict) else None
    else:
        tables = data.get("dataTableDTOList") if isinstance(data, dict) else None
    if not isinstance(tables, list):
        raise RuntimeError("Market data does not contain a verifiable quote table.")
    for item in tables:
        if not isinstance(item, dict) or item.get("dataTypeEnum") != "HQ":
            continue
        code = str(item.get("code") or "")
        if code != canonical_symbol:
            continue
        field = item.get("field")
        table = item.get("rawTable") or item.get("table")
        if not isinstance(field, dict) or not isinstance(table, dict):
            continue
        # A DAY series identifies the session, not the time of an intraday quote.
        if str(field.get("dateGranularity") or "").upper() == "DAY":
            continue
        if str(field.get("returnName") or "") != "最新价":
            continue
        # MX tables are keyed by the indicator's returnCode; returnSourceCode
        # identifies the underlying feed and need not match the table key.
        # Older direct responses omit returnCode and use the source code key.
        indicator_code = str(field.get("returnCode") or field.get("returnSourceCode") or "")
        times = table.get("headName")
        values = table.get(indicator_code)
        if (not isinstance(times, list) or not isinstance(values, list) or
                not times or len(times) != len(values)):
            continue
        try:
            # The last row is the table's latest point. An invalid/future
            # latest row must not be bypassed by an earlier plausible point.
            timestamp, value = times[-1], values[-1]
            # headName is a vendor source time, not the local receive time.
            # Policy assumption for MX HQ: a timezone-naive minute/second
            # timestamp is Beijing time. Date-only rows are never quotes.
            source_time = str(timestamp)
            if not re.fullmatch(
                r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"
                r"(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?",
                source_time,
            ):
                continue
            as_of = datetime.fromisoformat(source_time)
            last_price = float(value)
        except (TypeError, ValueError):
            continue
        if not isfinite(last_price) or last_price <= 0:
            continue
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=SHANGHAI)
        if timedelta(0) <= now - as_of.astimezone(SHANGHAI) <= timedelta(minutes=5):
            return {"symbol": canonical_symbol, "as_of": as_of.isoformat(),
                    "price": last_price, "source": "mx_query_market/HQ/latest_price"}
    raise RuntimeError("No independently timestamped, fresh quote for this symbol.")


def _vendor_data(payload: Any, purpose: str) -> dict[str, Any]:
    if not isinstance(payload, dict) or str(payload.get("code")) not in {"0", "200"}:
        raise RuntimeError(f"{purpose} response is unavailable or unsuccessful.")
    data = payload.get("data")
    # The observed balance contract carries rc=0. Positions omit rc, but
    # require a structurally valid posList in _available_shares below.
    if (not isinstance(data, dict) or
            (purpose == "Balance" and "rc" not in data) or
            ("rc" in data and str(data["rc"]) != "0")):
        raise RuntimeError(f"{purpose} response has no valid data.")
    return data


def _available_cash(client: Any) -> float:
    data = _vendor_data(client.get_balance(), "Balance")
    # Read-only unit comparison: moneyUnit=1 reports currencyUnit=1 and raw
    # availBalance; moneyUnit=1000 reports currencyUnit=1000 and 1000x the value.
    # This client requests moneyUnit=1, so the returned amount is already yuan.
    if type(data.get("currencyUnit")) is not int or data["currencyUnit"] != 1:
        raise RuntimeError("Balance currency unit is not verified as yuan.")
    try:
        raw_cash = data["availBalance"]
        if isinstance(raw_cash, bool):
            raise ValueError("boolean cash is invalid")
        cash = float(raw_cash)
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("Available cash is missing or malformed.") from exc
    if not isfinite(cash) or cash < 0:
        raise RuntimeError("Available cash is invalid.")
    return cash


def _available_shares(client: Any, symbol: str) -> int:
    data = _vendor_data(client.get_positions(), "Positions")
    rows = data.get("posList")
    if not isinstance(rows, list):
        raise RuntimeError("Positions response has no verifiable holdings list.")
    bare = symbol.split(".")[0]
    if not re.fullmatch("[0-9]{6}", bare):
        raise RuntimeError("Stock symbol must contain a six-digit A-share code.")
    if bare in SH_ETF_CODES:
        expected_market = 1
    elif bare.startswith(("0", "3", "6")):
        expected_market = 1 if bare.startswith("6") else 0
    else:
        raise RuntimeError("Unsupported exchange for sell order.")
    available = 0
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Positions response contains an invalid holding.")
        if str(row.get("secCode") or "") != bare:
            continue
        if row.get("secMkt") != expected_market:
            raise RuntimeError("Holding market does not match the stock symbol.")
        value = row.get("availCount")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError("Available shares are missing or malformed.")
        available += value
    return available


def submit_order(
    *, client: Any, settings: Any, action: str, symbol: str,
    quantity: int, price_type: str, price: float | None,
    manual_grant_id: int | None = None,
    manual_quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reserve an intent before the vendor call; uncertain outcomes are never retried."""
    if str(getattr(settings, "run_type", "")) != "trade":
        raise RuntimeError("Trading is only available in a trade run.")
    if action not in {"BUY", "SELL"}:
        raise RuntimeError("Only BUY and SELL order actions are supported.")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or quantity % 100:
        raise RuntimeError("Order quantity must be a positive multiple of 100 shares/units.")
    if not isinstance(symbol, str) or not re.fullmatch(r"[0-9]{6}(?:\.(?:SH|SZ))?", symbol):
        raise RuntimeError("Order symbol must be a supported six-digit stock or ETF code.")
    bare_symbol = symbol.split(".")[0]
    is_etf = bare_symbol in SH_ETF_CODES
    if not is_etf and not re.fullmatch(r"[036][0-9]{5}", bare_symbol):
        raise RuntimeError("Order symbol is not a supported A-share or historically verified ETF.")
    suffix = "SH" if is_etf or bare_symbol.startswith("6") else "SZ"
    if "." in symbol and symbol.rsplit(".", 1)[1] != suffix:
        raise RuntimeError("Symbol exchange suffix does not match its security code.")
    symbol = f"{bare_symbol}.{suffix}"
    run_id = int(getattr(settings, "run_id", 0) or 0)
    settings_id = int(getattr(settings, "id", 0) or 0)
    if not run_id or not settings_id:
        raise RuntimeError("Trading requires a persisted run and settings.")
    if manual_grant_id is not None and (type(manual_grant_id) is not int or manual_grant_id <= 0):
        raise RuntimeError("Invalid manual order grant.")
    if manual_quote is not None and manual_grant_id is None:
        raise RuntimeError("A preverified quote requires an exact manual grant.")
    if is_etf and manual_grant_id is not None:
        # Existing one-shot grants store cents, not the ETF's mill-yuan tick.
        raise RuntimeError("ETF manual grants require a mill-precision grant schema.")
    with session_scope() as db:
        current = db.get(AppSettings, settings_id)
        if manual_grant_id is None:
            if current is None or not current.trade_enabled:
                raise RuntimeError("Trading is paused; configure limits and explicitly enable it.")
            if current.risk_cash_only:
                if current.risk_max_order_value is not None or current.risk_max_daily_value is not None:
                    raise RuntimeError("Cash-only policy conflicts with monetary limits.")
            elif current.risk_max_order_value is None or current.risk_max_daily_value is None:
                raise RuntimeError("Risk limits are incomplete; trading remains paused.")
        elif current is None or current.trade_enabled:
            raise RuntimeError("Scoped manual orders require the global trading gate to remain closed.")
    now = datetime.now(SHANGHAI)
    if not trading_calendar_service.is_trading_day(now.date()):
        raise RuntimeError("Orders are disabled outside verified trading days.")
    clock = now.hour * 60 + now.minute
    if not (570 <= clock < 690 or 780 <= clock < 900):
        raise RuntimeError("Orders are disabled outside regular A-share trading hours.")
    if (price_type != "LIMIT" or isinstance(price, bool) or
            not isinstance(price, (int, float)) or not isfinite(price) or price <= 0):
        raise RuntimeError("Only positive LIMIT orders can pass deterministic notional checks.")
    # JSON callers can supply 10 or 10.0 for the same order. Hash one
    # numeric representation so the intent lock cannot be bypassed.
    price = float(price)
    price_in_cents = Decimal(str(price)) * 100
    price_in_ticks = Decimal(str(price)) * (1000 if is_etf else 100)
    if price_in_ticks != price_in_ticks.to_integral_value():
        raise RuntimeError("ETF limit price must have mill precision." if is_etf
                           else "A-share limit price must have cent precision.")

    if manual_quote is None:
        quote = _verified_market_quote(client, symbol, now, asset_type="etf" if is_etf else "stock")
    else:
        try:
            as_of = datetime.fromisoformat(str(manual_quote["as_of"]))
            quote_price = Decimal(str(manual_quote["price"]))
            valid = (manual_quote.get("symbol") == symbol
                     and manual_quote.get("source") == "mx_query_market/HQ/latest_price"
                     and as_of.tzinfo is not None
                     and timedelta(0) <= now - as_of.astimezone(SHANGHAI) <= timedelta(minutes=5)
                     and quote_price.is_finite() and quote_price == Decimal(str(price)))
        except (KeyError, TypeError, ValueError, ArithmeticError):
            valid = False
        if not valid:
            raise RuntimeError("Preverified manual quote is stale or mismatches the authorized order.")
        quote = manual_quote
    if manual_grant_id is not None and action == "BUY" and Decimal(str(quote["price"])) < Decimal(str(price)):
        raise RuntimeError("Fresh quote fell below the authorized buy limit; stop this batch.")
    as_of = datetime.fromisoformat(quote["as_of"])
    notional = quantity * price
    if not isfinite(notional):
        raise RuntimeError("Order notional is not finite.")
    available_cash = _available_cash(client) if action == "BUY" else None
    available_shares = _available_shares(client, symbol) if action == "SELL" else None
    if available_cash is not None and notional > available_cash:
        raise RuntimeError("Order notional exceeds available cash.")
    if available_shares is not None and quantity > available_shares:
        raise RuntimeError("Sell quantity exceeds available shares.")
    trade_date = now.date().isoformat()
    def intent_hash(code: str, limit_price: float | int) -> str:
        return hashlib.sha256(json.dumps(
            [trade_date, action, code, quantity, price_type, limit_price],
            separators=(",", ":"),
        ).encode()).hexdigest()

    intent_key = intent_hash(symbol, price)
    # Older gateways accepted bare codes and integer price representations as
    # distinct keys. Block equivalent persisted intents across those formats.
    matching_intent_keys = {intent_key, intent_hash(bare_symbol, price)}
    if price.is_integer():
        matching_intent_keys.update((
            intent_hash(symbol, int(price)),
            intent_hash(bare_symbol, int(price)),
        ))

    with session_scope() as db:
        # SQLite's deferred read transaction does not serialize aggregate checks.
        # Reserve the write lock before reading limits and existing intents.
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        current = db.get(AppSettings, settings_id)
        if manual_grant_id is None:
            if current is None or not current.trade_enabled:
                raise RuntimeError("Trading is paused; configure limits and explicitly enable it.")
            cash_only = bool(current.risk_cash_only)
            max_order = current.risk_max_order_value
            max_daily = current.risk_max_daily_value
            if cash_only and (max_order is not None or max_daily is not None):
                raise RuntimeError("Cash-only policy conflicts with monetary limits.")
        else:
            if current is None or current.trade_enabled:
                raise RuntimeError("Scoped manual orders require the global trading gate to remain closed.")
            run = db.get(StrategyRun, run_id)
            grant = db.get(ManualOrderGrant, manual_grant_id)
            if (run is None or run.trigger_source != "manual" or run.run_type != "trade"
                    or run.schedule_id is not None or grant is None
                    or grant.run_id != run_id or grant.consumed_at is not None
                    or grant.trade_date != trade_date
                    or grant.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None)
                    or grant.symbol != symbol or grant.action != action
                    or grant.quantity != quantity or price_type != "LIMIT"
                    or grant.price_cents != int(price_in_cents)):
                raise RuntimeError("Manual order grant is expired, used, or does not match this order.")
            max_order = grant.max_order_cents / 100
            max_daily = grant.max_daily_cents / 100
            cash_only = False
        if not cash_only:
            if not all(isinstance(value, (int, float)) and isfinite(value) and value > 0
                       for value in (max_order, max_daily)):
                raise RuntimeError("Risk limits are incomplete; trading remains paused.")
            if notional > max_order:
                raise RuntimeError("Order notional exceeds the configured per-order limit.")
        if db.scalar(select(TradeIntent.id).where(
            TradeIntent.intent_key.in_(matching_intent_keys)
        )):
            raise RuntimeError("Duplicate order intent; reconcile the existing order first.")
        active_intents = (
            TradeIntent.trade_date == trade_date,
            TradeIntent.status.in_(("reserved", "ambiguous", "response_received")),
        )
        if available_cash is not None:
            pending_buys = db.scalar(select(func.coalesce(func.sum(TradeIntent.notional), 0)).where(
                *active_intents, TradeIntent.action == "BUY",
            )) or 0
            if notional + pending_buys > available_cash:
                raise RuntimeError("Order plus pending buys exceeds available cash.")
        if available_shares is not None:
            pending_sells = db.scalar(select(func.coalesce(func.sum(TradeIntent.quantity), 0)).where(
                *active_intents, TradeIntent.action == "SELL", TradeIntent.symbol == symbol,
            )) or 0
            if quantity + pending_sells > available_shares:
                raise RuntimeError("Order plus pending sells exceeds available shares.")
        if not cash_only:
            committed = db.scalar(select(func.coalesce(func.sum(TradeIntent.notional), 0)).where(
                *active_intents,
            )) or 0
            if committed + notional > max_daily:
                raise RuntimeError("Order exceeds the configured daily notional limit.")
        count = db.scalar(select(func.count(TradeIntent.id)).where(TradeIntent.run_id == run_id)) or 0
        if count >= int(current.max_actions or 0):
            raise RuntimeError("Run has reached the configured maximum action count.")
        intent = TradeIntent(
            run_id=run_id, intent_key=intent_key, trade_date=trade_date,
            symbol=symbol, action=action, quantity=quantity,
            price=price, notional=notional, status="reserved",
        )
        db.add(intent)
        try:
            db.flush()
        except IntegrityError as exc:
            raise RuntimeError("Duplicate order intent; reconcile the existing order first.") from exc
        intent_id = intent.id
        if manual_grant_id is not None:
            grant.consumed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            grant.intent_id = intent_id

    observer = getattr(settings, "trade_observer", None)
    if callable(observer):
        try:
            observer({
                "intent_id": intent_id, "run_id": run_id, "action": action,
                "symbol": symbol.split(".")[0], "quantity": quantity, "price": price,
                "as_of": as_of.isoformat(),
                "evidence": [{
                    "source": "market_quote", "stance": "neutral",
                    "as_of": as_of.isoformat(),
                    "fact": f"Latest verified quote price: {quote['price']} yuan ({quote['source']}).",
                }],
            })
        except Exception:
            logger.exception("Trade observer failed for intent_id=%s", intent_id)

    try:
        result = client.trade(
            action=action, symbol=bare_symbol, quantity=quantity,
            price_type=price_type, price=price,
        )
    except Exception:
        with session_scope() as db:
            intent = db.get(TradeIntent, intent_id)
            intent.status = "ambiguous"
        raise RuntimeError("Order submission outcome is unknown; reconcile before retrying.") from None
    vendor_code = str(result.get("code")) if isinstance(result, dict) and "code" in result else None
    order_data = result.get("data") if isinstance(result, dict) else None
    inner_rc = str(order_data.get("rc")) if isinstance(order_data, dict) and "rc" in order_data else None
    rejected = (vendor_code is not None and vendor_code not in {"0", "200"}) or (
        inner_rc is not None and inner_rc != "0"
    )
    order_id = (result.get("orderId") or (
        order_data.get("orderID") or order_data.get("orderId")
        if isinstance(order_data, dict) else None
    )) if isinstance(result, dict) else None
    # Actual MX responses contain data.rc, secCode, secMkt and orderID.
    # A code=200 response with missing/mismatched fields is ambiguous, never success.
    verified_vendor_details = (
        vendor_code is None or (
            inner_rc == "0" and isinstance(order_data, dict)
            and order_data.get("secCode") == bare_symbol
            and order_data.get("secMkt") == (1 if suffix == "SH" else 0)
        )
    )
    uncertain = not rejected and (not order_id or not verified_vendor_details)
    with session_scope() as db:
        intent = db.get(TradeIntent, intent_id)
        intent.status = "rejected" if rejected else "ambiguous" if uncertain else "response_received"
        intent.response_payload = result if isinstance(result, dict) else {"raw": str(result)}
    if rejected:
        raise RuntimeError("Vendor rejected the order; see the persisted intent response.")
    if uncertain:
        raise RuntimeError("Vendor response did not confirm an order; reconcile before retrying.")
    return result
