from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.email_notification_service import (  # noqa: E402
    PLACEHOLDER_SMTP_PASSWORD,
    EmailNotificationService,
)


def _settings(**overrides):
    values = {
        "email_alerts_enabled": True,
        "email_smtp_host": "smtp.qq.com",
        "email_smtp_port": 465,
        "email_smtp_use_ssl": True,
        "email_smtp_username": "sender@qq.com",
        "email_smtp_password": "auth-code",
        "email_from": "sender@qq.com",
        "email_to": ["target@qq.com"],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _orders():
    return [
        {
            "symbol": "300059",
            "action": "BUY",
            "quantity": 100,
            "price_type": "MARKET",
            "price": None,
            "status": "submitted",
        },
        {
            "symbol": "600519",
            "action": "SELL",
            "quantity": 50,
            "price_type": "LIMIT",
            "price": 123.45,
            "status": "submitted",
        },
    ]


class FakeSMTP:
    instances = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.login_args = None
        self.messages = []
        self.started_tls = False
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.messages.append(message)


class FailingSMTP(FakeSMTP):
    def send_message(self, message):
        raise RuntimeError("smtp boom")


def test_disabled_email_alerts_skip_sending() -> None:
    FakeSMTP.instances = []
    service = EmailNotificationService(smtp_ssl_factory=FakeSMTP)

    sent = service.send_trade_alert(
        settings=_settings(email_alerts_enabled=False),
        run_id=1,
        trigger_source="manual",
        orders=_orders(),
    )

    assert sent is False
    assert FakeSMTP.instances == []


def test_placeholder_password_skips_sending() -> None:
    FakeSMTP.instances = []
    service = EmailNotificationService(smtp_ssl_factory=FakeSMTP)

    sent = service.send_trade_alert(
        settings=_settings(email_smtp_password=PLACEHOLDER_SMTP_PASSWORD),
        run_id=1,
        trigger_source="manual",
        orders=_orders(),
    )

    assert sent is False
    assert FakeSMTP.instances == []


def test_trade_alert_sends_qq_smtp_email() -> None:
    FakeSMTP.instances = []
    service = EmailNotificationService(smtp_ssl_factory=FakeSMTP)

    sent = service.send_trade_alert(
        settings=_settings(email_to="target@qq.com;backup@qq.com"),
        run_id=42,
        trigger_source="schedule",
        schedule_name="ETF上午运行1号",
        orders=_orders(),
        final_answer="执行两笔模拟交易。",
    )

    assert sent is True
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port, smtp.timeout) == ("smtp.qq.com", 465, 20)
    assert smtp.login_args == ("sender@qq.com", "auth-code")
    message = smtp.messages[0]
    assert message["From"] == "sender@qq.com"
    assert message["To"] == "target@qq.com, backup@qq.com"
    assert "Aniu模拟交易提醒" in message["Subject"]
    body = message.get_content()
    assert "运行ID: 42" in body
    assert "触发来源: 定时触发" in body
    assert "定时任务: ETF上午运行1号" in body
    assert "买入 300059 数量: 100 价格: 市价 状态: submitted" in body
    assert "卖出 600519 数量: 50 价格: LIMIT 123.45 状态: submitted" in body
    assert "执行两笔模拟交易。" in body


def test_smtp_failure_is_isolated() -> None:
    service = EmailNotificationService(smtp_ssl_factory=FailingSMTP)

    sent = service.send_trade_alert(
        settings=_settings(),
        run_id=7,
        trigger_source="manual",
        orders=_orders(),
    )

    assert sent is False
