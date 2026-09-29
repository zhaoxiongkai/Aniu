"""Local one-time operator command for the five explicitly authorized mock buys.

Run only in the application container: python -m app.services.manual_mock_buy --execute
This module is intentionally not exposed as an HTTP or agent tool.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select

from app.db.database import init_db, session_scope
from app.db.models import AppSettings, ManualOrderGrant, StrategyRun, TradeIntent
from app.services.order_gateway import SHANGHAI, _available_cash, _verified_market_quote, submit_order
from app.services.trading_calendar_service import trading_calendar_service
from app.skills.providers import build_skill_context
from skills.mx_core.client import MXClient


SYMBOLS = ("600460.SH", "600522.SH", "600657.SH", "601808.SH", "600547.SH")
QUANTITY = 100
# Read-only MX cumulative-volume observations at 2026-09-24 12:04 Beijing time.
# Require each feed to advance after the lunch break; a moving HQ headName
# alone was observed during lunch and is not a last-trade timestamp.
LUNCH_VOLUME = {
    "600460.SH": 30332958, "600522.SH": 75621655,
    "600657.SH": 41898142, "601808.SH": 5542884,
    "600547.SH": 49452272,
}


def _post_lunch_volume_advanced(client: MXClient, symbol: str, now: datetime) -> bool:
    payload = client.query_market(f"{symbol} 成交量")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or str(payload.get("code")) not in {"0", "200"}:
        return False
    inner = data.get("data")
    if isinstance(inner, dict) and str(data.get("code")) not in {"0", "200"}:
        return False
    tables = (inner.get("searchDataResultDTO") or {}).get("dataTableDTOList") if isinstance(inner, dict) else data.get("dataTableDTOList")
    if not isinstance(tables, list):
        return False
    for row in tables:
        if not isinstance(row, dict) or row.get("code") != symbol or row.get("dataTypeEnum") != "HQ":
            continue
        field = row.get("field") or {}
        table = row.get("rawTable") or {}
        if field.get("returnName") != "成交量" or not isinstance(table, dict):
            continue
        times = table.get("headName") or []
        values = table.get(field.get("returnCode") or field.get("returnSourceCode")) or []
        if (not isinstance(times, list) or not isinstance(values, list)
                or not times or len(times) != len(values)):
            continue
        try:
            as_of = datetime.fromisoformat(str(times[-1]))
            as_of = (as_of.replace(tzinfo=SHANGHAI) if as_of.tzinfo is None
                     else as_of.astimezone(SHANGHAI))
            volume = int(values[-1])
        except (IndexError, TypeError, ValueError):
            continue
        if (as_of.date() == now.date() and as_of.hour >= 13
                and timedelta(0) <= now - as_of <= timedelta(minutes=5)
                and volume > LUNCH_VOLUME[symbol]):
            return True
    return False


def _session_open(now: datetime) -> bool:
    minute = now.hour * 60 + now.minute
    return (570 <= minute < 690 or 780 <= minute < 900)


def _check_prior_grants(db, trade_date: str, allow_safe_retry: bool) -> None:
    prior = db.scalars(select(ManualOrderGrant).where(
        ManualOrderGrant.trade_date == trade_date,
        ManualOrderGrant.symbol.in_(SYMBOLS),
    ).order_by(ManualOrderGrant.id)).all()
    if not prior:
        if allow_safe_retry:
            raise RuntimeError("No previously failed five-order batch to retry.")
        return
    if (not allow_safe_retry or len(prior) != len(SYMBOLS)
            or tuple(g.symbol for g in prior) != SYMBOLS
            or any(g.action != "BUY" or g.quantity != QUANTITY
                   or g.consumed_at is None or g.intent_id is not None for g in prior)
            or [db.get(StrategyRun, g.run_id).status for g in prior]
               != ["failed", "skipped", "skipped", "skipped", "skipped"]):
        raise RuntimeError("Previous grant batch is not a verified no-order failure; no retry.")


def execute(*, allow_safe_retry: bool = False) -> list[dict[str, object]]:
    init_db()
    now = datetime.now(SHANGHAI)
    if now.date().isoformat() != "2026-09-24":
        raise RuntimeError("This one-time authorization is limited to 2026-09-24.")
    if not trading_calendar_service.is_trading_day(now.date()) or not _session_open(now):
        raise RuntimeError("Not a verified regular A-share trading session; no orders created.")
    with session_scope() as db:
        settings = db.scalar(select(AppSettings).order_by(AppSettings.id).limit(1))
        if settings is None or settings.trade_enabled:
            raise RuntimeError("Global trading gate must remain closed.")
        settings_id = settings.id
        config = build_skill_context(run_type="trade", app_settings=settings)["mx_client_config"]
        if not config.get("api_key"):
            raise RuntimeError("MX API key is not configured.")
        existing = db.scalars(select(TradeIntent).where(
            TradeIntent.trade_date == now.date().isoformat(),
            TradeIntent.symbol.in_(SYMBOLS),
        )).all()
        if existing:
            raise RuntimeError("An intent already exists for a requested symbol today; reconcile first.")
        _check_prior_grants(db, now.date().isoformat(), allow_safe_retry)

    with MXClient(api_key=config["api_key"], base_url=config.get("base_url")) as client:
        prices: dict[str, int] = {}
        verified_quotes: dict[str, dict[str, object]] = {}
        for symbol in SYMBOLS:
            quote = _verified_market_quote(client, symbol, datetime.now(SHANGHAI))
            if not _post_lunch_volume_advanced(client, symbol, datetime.now(SHANGHAI)):
                raise RuntimeError(f"{symbol}: no verified post-lunch volume advancement.")
            cents = Decimal(str(quote["price"])) * 100
            if cents != cents.to_integral_value() or cents <= 0:
                raise RuntimeError(f"{symbol}: quote cannot be used as a cent-precision limit.")
            prices[symbol] = int(cents)
            verified_quotes[symbol] = quote
        total_cents = sum(QUANTITY * cents for cents in prices.values())
        if total_cents / 100 > _available_cash(client):
            raise RuntimeError("Insufficient verified cash for the complete five-order batch.")
        orders = client.get_orders()
        order_data = orders.get("data") if isinstance(orders, dict) else None
        if (str(orders.get("code")) not in {"0", "200"} or not isinstance(order_data, dict)
                or str(order_data.get("rc")) != "0" or not isinstance(order_data.get("orders"), list)):
            raise RuntimeError("Cannot verify vendor order list before submission.")
        if (type(order_data.get("totalNum")) is not int
                or order_data["totalNum"] != len(order_data["orders"])
                or not all(isinstance(row, dict) for row in order_data["orders"])):
            raise RuntimeError("Vendor order list is incomplete; no orders submitted.")
        if any(str(row.get("secCode")) in {s[:6] for s in SYMBOLS}
               for row in order_data["orders"] if isinstance(row, dict)):
            raise RuntimeError("A vendor order exists for a requested symbol; reconcile first.")
        print(json.dumps({"limit_prices_yuan": {
            symbol: f"{cents / 100:.2f}" for symbol, cents in prices.items()
        }, "quantity_each": QUANTITY, "maximum_batch_yuan": f"{total_cents / 100:.2f}"},
            ensure_ascii=False), flush=True)

        # Reserve exact, short-lived authorizations before any vendor write.
        # If a later order fails, unused grants expire and are never retried here.
        grant_ids: list[tuple[str, int, int, int]] = []
        with session_scope() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            current = db.get(AppSettings, settings_id)
            if current is None or current.trade_enabled:
                raise RuntimeError("Global trading gate changed; no orders created.")
            if db.scalar(select(TradeIntent.id).where(
                TradeIntent.trade_date == now.date().isoformat(),
                TradeIntent.symbol.in_(SYMBOLS),
            )):
                raise RuntimeError("Existing order intent detected; reconcile first.")
            _check_prior_grants(db, now.date().isoformat(), allow_safe_retry)
            expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=90)
            for symbol in SYMBOLS:
                run = StrategyRun(trigger_source="manual", run_type="trade",
                                  schedule_id=None, status="running")
                db.add(run)
                db.flush()
                grant = ManualOrderGrant(
                    run_id=run.id, trade_date=now.date().isoformat(),
                    symbol=symbol, action="BUY", quantity=QUANTITY,
                    price_cents=prices[symbol], max_order_cents=QUANTITY * prices[symbol],
                    max_daily_cents=total_cents, expires_at=expires,
                )
                db.add(grant)
                db.flush()
                grant_ids.append((symbol, prices[symbol], run.id, grant.id))

        outcomes: list[dict[str, object]] = []
        for index, (symbol, cents, run_id, grant_id) in enumerate(grant_ids):
            settings_view = SimpleNamespace(id=settings_id, run_id=run_id, run_type="trade")
            try:
                response = submit_order(
                    client=client, settings=settings_view, action="BUY", symbol=symbol,
                    quantity=QUANTITY, price_type="LIMIT", price=cents / 100,
                    manual_grant_id=grant_id, manual_quote=verified_quotes[symbol],
                )
                data = response.get("data") if isinstance(response, dict) else None
                order_id = response.get("orderId") or (
                    (data.get("orderID") or data.get("orderId")) if isinstance(data, dict) else None
                )
                result = {"symbol": symbol, "limit_yuan": f"{cents / 100:.2f}",
                          "run_id": run_id, "status": "response_received", "order_id": order_id}
                with session_scope() as db:
                    run = db.get(StrategyRun, run_id)
                    run.status = "completed"
                    run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            except Exception as exc:
                with session_scope() as db:
                    run = db.get(StrategyRun, run_id)
                    run.status = "failed"
                    run.error_message = str(exc)
                    run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    failed_grant = db.get(ManualOrderGrant, grant_id)
                    if failed_grant.consumed_at is None:
                        failed_grant.consumed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                    for _, _, pending_run_id, pending_grant_id in grant_ids[index + 1:]:
                        pending = db.get(ManualOrderGrant, pending_grant_id)
                        if pending.consumed_at is None:
                            pending.consumed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                        pending_run = db.get(StrategyRun, pending_run_id)
                        pending_run.status = "skipped"
                        pending_run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
                result = {"symbol": symbol, "limit_yuan": f"{cents / 100:.2f}",
                          "run_id": run_id, "status": "stopped", "error": str(exc)}
                outcomes.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                break
            outcomes.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        return outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Submit the five authorized mock buys")
    parser.add_argument("--retry-after-safe-failure", action="store_true",
                        help="Use only after explicit new authorization and verified zero-order first failure")
    args = parser.parse_args()
    if not args.execute:
        parser.error("Explicit --execute is required; no vendor write was sent.")
    execute(allow_safe_retry=args.retry_after_safe_failure)


if __name__ == "__main__":
    main()
