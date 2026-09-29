from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import database
from app.db.models import AppSettings, Base, ManualOrderGrant, StrategyRun, TradeIntent
from app.services import manual_mock_buy, order_gateway


class Afternoon(datetime):
    @classmethod
    def now(cls, tz=None):
        point = datetime(2026, 9, 24, 13, 5, tzinfo=manual_mock_buy.SHANGHAI)
        return point.astimezone(tz) if tz is not None else point.replace(tzinfo=None)


class FakeVendor:
    calls = 0
    fail_second = False

    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def query_market(self, query):
        symbol = query.split()[0]
        return {"code": "200", "data": {"dataTableDTOList": [{
            "code": symbol, "dataTypeEnum": "HQ",
            "field": {"returnName": "最新价", "returnSourceCode": "f2"},
            "rawTable": {"headName": ["2026-09-24T13:04:00+08:00"], "f2": [10.0]},
        }]}}

    def get_balance(self):
        return {"code": "200", "data": {"rc": 0, "currencyUnit": 1, "availBalance": 200000}}

    def get_orders(self):
        return {"code": "200", "data": {"rc": 0, "totalNum": 0, "orders": []}}

    def trade(self, **kwargs):
        type(self).calls += 1
        if self.fail_second and type(self).calls == 2:
            raise TimeoutError("vendor timeout")
        return {"code": 200, "data": {
            "rc": 0, "secCode": kwargs["symbol"], "secMkt": 1,
            "orderID": f"SIM-{type(self).calls}",
        }}


@pytest.fixture
def isolated_batch(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'manual.db').as_posix()}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "_engine", engine)
    monkeypatch.setattr(database, "_session_local", sessionmaker(bind=engine, expire_on_commit=False))
    with database.session_scope() as db:
        db.add(AppSettings(id=1, trade_enabled=False, max_actions=2))
    monkeypatch.setattr(manual_mock_buy, "init_db", lambda: None)
    monkeypatch.setattr(manual_mock_buy, "datetime", Afternoon)
    monkeypatch.setattr(order_gateway, "datetime", Afternoon)
    monkeypatch.setattr(manual_mock_buy.trading_calendar_service, "is_trading_day", lambda date: True)
    monkeypatch.setattr(manual_mock_buy, "build_skill_context", lambda **kwargs: {
        "mx_client_config": {"api_key": "fake", "base_url": "https://invalid.example"}
    })
    monkeypatch.setattr(manual_mock_buy, "MXClient", FakeVendor)
    monkeypatch.setattr(manual_mock_buy, "_post_lunch_volume_advanced", lambda *args: True)
    FakeVendor.calls = 0
    FakeVendor.fail_second = False
    yield
    engine.dispose()


def test_one_time_five_order_batch_keeps_global_gate_closed(isolated_batch):
    outcomes = manual_mock_buy.execute()
    assert len(outcomes) == 5
    assert all(item["status"] == "response_received" for item in outcomes)
    assert FakeVendor.calls == 5
    with database.session_scope() as db:
        assert db.get(AppSettings, 1).trade_enabled is False
        assert len(db.scalars(select(TradeIntent)).all()) == 5
        assert all(g.consumed_at and g.intent_id for g in db.scalars(select(ManualOrderGrant)).all())
        assert all(r.trigger_source == "manual" and r.schedule_id is None
                   for r in db.scalars(select(StrategyRun)).all())
    with pytest.raises(RuntimeError, match="intent already exists"):
        manual_mock_buy.execute()
    assert FakeVendor.calls == 5


def test_ambiguous_second_order_stops_and_revokes_rest(isolated_batch):
    FakeVendor.fail_second = True
    outcomes = manual_mock_buy.execute()
    assert len(outcomes) == 2
    assert outcomes[0]["status"] == "response_received"
    assert outcomes[1]["status"] == "stopped"
    assert FakeVendor.calls == 2
    with database.session_scope() as db:
        assert [i.status for i in db.scalars(select(TradeIntent).order_by(TradeIntent.id))] == [
            "response_received", "ambiguous",
        ]
        grants = db.scalars(select(ManualOrderGrant).order_by(ManualOrderGrant.id)).all()
        assert len(grants) == 5 and all(g.consumed_at for g in grants)
    with pytest.raises(RuntimeError, match="intent already exists"):
        manual_mock_buy.execute()
    assert FakeVendor.calls == 2


def test_explicit_retry_only_after_consumed_zero_intent_batch(isolated_batch):
    with database.session_scope() as db:
        for index, symbol in enumerate(manual_mock_buy.SYMBOLS):
            run = StrategyRun(trigger_source="manual", run_type="trade", schedule_id=None,
                              status="failed" if index == 0 else "skipped")
            db.add(run)
            db.flush()
            db.add(ManualOrderGrant(
                run_id=run.id, trade_date="2026-09-24", symbol=symbol,
                action="BUY", quantity=100, price_cents=1000,
                max_order_cents=100000, max_daily_cents=500000,
                expires_at=datetime(2026, 9, 24, 5, 6),
                consumed_at=datetime(2026, 9, 24, 5, 5), intent_id=None,
            ))
    with pytest.raises(RuntimeError, match="no-order failure"):
        manual_mock_buy.execute()
    assert FakeVendor.calls == 0
    outcomes = manual_mock_buy.execute(allow_safe_retry=True)
    assert len(outcomes) == 5 and FakeVendor.calls == 5
    with database.session_scope() as db:
        assert len(db.scalars(select(ManualOrderGrant)).all()) == 10
        assert len(db.scalars(select(TradeIntent)).all()) == 5
    with pytest.raises(RuntimeError, match="intent already exists"):
        manual_mock_buy.execute(allow_safe_retry=True)


def test_retry_denied_if_previous_grant_has_intent(isolated_batch):
    with database.session_scope() as db:
        for index, symbol in enumerate(manual_mock_buy.SYMBOLS):
            run = StrategyRun(trigger_source="manual", run_type="trade", schedule_id=None,
                              status="failed" if index == 0 else "skipped")
            db.add(run)
            db.flush()
            db.add(ManualOrderGrant(
                run_id=run.id, trade_date="2026-09-24", symbol=symbol,
                action="BUY", quantity=100, price_cents=1000,
                max_order_cents=100000, max_daily_cents=500000,
                expires_at=datetime(2026, 9, 24, 5, 6),
                consumed_at=datetime(2026, 9, 24, 5, 5), intent_id=1 if index == 0 else None,
            ))
    with pytest.raises(RuntimeError, match="no-order failure"):
        manual_mock_buy.execute(allow_safe_retry=True)
    assert FakeVendor.calls == 0


@pytest.mark.parametrize("volume_delta,head_name,expected", [
    (1, "2026-09-24 13:04", True),
    (1, "2026-09-24T05:04:00+00:00", True),
    (0, "2026-09-24 13:04", False),
    (1, "2026-09-24 12:04", False),
    (1, "2026-09-23 13:04", False),
])
def test_post_lunch_volume_requires_new_afternoon_data(volume_delta, head_name, expected):
    symbol = "600460.SH"

    class VolumeClient:
        def query_market(self, query):
            assert query == f"{symbol} 成交量"
            return {"code": 0, "data": {"code": 0, "data": {
                "searchDataResultDTO": {"dataTableDTOList": [{
                    "code": symbol, "dataTypeEnum": "HQ",
                    "field": {"returnName": "成交量", "returnCode": "CJL_f5_3"},
                    "rawTable": {"headName": [head_name],
                                 "CJL_f5_3": [str(manual_mock_buy.LUNCH_VOLUME[symbol] + volume_delta)]},
                }]},
            }}}

    now = datetime(2026, 9, 24, 13, 5, tzinfo=manual_mock_buy.SHANGHAI)
    assert manual_mock_buy._post_lunch_volume_advanced(VolumeClient(), symbol, now) is expected
