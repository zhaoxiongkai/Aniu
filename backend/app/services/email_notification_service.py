from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any, Callable, Mapping, Sequence


logger = logging.getLogger(__name__)

PLACEHOLDER_SMTP_PASSWORD = "replace-with-new-qq-mail-auth-code"
FINAL_ANSWER_MAX_CHARS = 800


class EmailNotificationService:
    def __init__(
        self,
        *,
        smtp_factory: Callable[..., Any] = smtplib.SMTP,
        smtp_ssl_factory: Callable[..., Any] = smtplib.SMTP_SSL,
    ) -> None:
        self._smtp_factory = smtp_factory
        self._smtp_ssl_factory = smtp_ssl_factory

    def send_trade_alert(
        self,
        *,
        settings: Any,
        run_id: int,
        trigger_source: str,
        orders: Sequence[Mapping[str, Any]],
        final_answer: str | None = None,
        schedule_name: str | None = None,
    ) -> bool:
        valid_orders = [
            order
            for order in orders
            if str(order.get("action") or "").upper() in {"BUY", "SELL"}
        ]
        if not valid_orders:
            return False

        if not bool(getattr(settings, "email_alerts_enabled", False)):
            return False

        config = self._get_config(settings)
        if config is None:
            logger.info("email trade alert skipped: SMTP config is incomplete")
            return False

        message = self._build_trade_alert_message(
            sender=config["sender"],
            recipients=config["recipients"],
            run_id=run_id,
            trigger_source=trigger_source,
            orders=valid_orders,
            final_answer=final_answer,
            schedule_name=schedule_name,
        )

        try:
            if config["use_ssl"]:
                with self._smtp_ssl_factory(
                    config["host"],
                    config["port"],
                    timeout=20,
                ) as smtp:
                    smtp.login(config["username"], config["password"])
                    smtp.send_message(message)
            else:
                with self._smtp_factory(
                    config["host"],
                    config["port"],
                    timeout=20,
                ) as smtp:
                    smtp.starttls()
                    smtp.login(config["username"], config["password"])
                    smtp.send_message(message)
        except Exception as exc:
            logger.warning(
                "email trade alert failed: run_id=%s host=%s port=%s error=%s",
                run_id,
                config["host"],
                config["port"],
                exc,
            )
            return False

        logger.info(
            "email trade alert sent: run_id=%s recipients=%d orders=%d",
            run_id,
            len(config["recipients"]),
            len(valid_orders),
        )
        return True

    def _get_config(self, settings: Any) -> dict[str, Any] | None:
        host = self._strip(getattr(settings, "email_smtp_host", None))
        username = self._strip(getattr(settings, "email_smtp_username", None))
        password = self._strip(getattr(settings, "email_smtp_password", None))
        sender = self._strip(getattr(settings, "email_from", None))
        recipients = self._normalize_recipients(getattr(settings, "email_to", None))

        if password == PLACEHOLDER_SMTP_PASSWORD:
            logger.info("email trade alert skipped: SMTP password placeholder is still set")
            return None
        if not all([host, username, password, sender]) or not recipients:
            return None

        return {
            "host": host,
            "port": int(getattr(settings, "email_smtp_port", 465) or 465),
            "use_ssl": bool(getattr(settings, "email_smtp_use_ssl", True)),
            "username": username,
            "password": password,
            "sender": sender,
            "recipients": recipients,
        }

    def _build_trade_alert_message(
        self,
        *,
        sender: str,
        recipients: Sequence[str],
        run_id: int,
        trigger_source: str,
        orders: Sequence[Mapping[str, Any]],
        final_answer: str | None = None,
        schedule_name: str | None = None,
    ) -> EmailMessage:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = ", ".join(recipients)
        message["Subject"] = self._build_subject(orders)
        message.set_content(
            self._build_body(
                run_id=run_id,
                trigger_source=trigger_source,
                schedule_name=schedule_name,
                orders=orders,
                final_answer=final_answer,
            ),
            subtype="plain",
            charset="utf-8",
        )
        return message

    def _build_subject(self, orders: Sequence[Mapping[str, Any]]) -> str:
        action_names = {
            self._action_text(str(order.get("action") or ""))
            for order in orders
        }
        action_text = "/".join(sorted(action_names)) if action_names else "买入/卖出"
        return f"Aniu模拟交易提醒：{action_text} {len(orders)} 笔"

    def _build_body(
        self,
        *,
        run_id: int,
        trigger_source: str,
        schedule_name: str | None,
        orders: Sequence[Mapping[str, Any]],
        final_answer: str | None,
    ) -> str:
        trigger_text = "定时触发" if str(trigger_source).strip() == "schedule" else "手动触发"
        lines = [
            "Aniu 已记录模拟交易订单。",
            "",
            f"运行ID: {run_id}",
            f"触发来源: {trigger_text}",
        ]
        if self._strip(schedule_name):
            lines.append(f"定时任务: {self._strip(schedule_name)}")

        lines.extend(["", "订单明细:"])
        for index, order in enumerate(orders, start=1):
            lines.append(
                "{index}. {action} {symbol} 数量: {quantity} 价格: {price} 状态: {status}".format(
                    index=index,
                    action=self._action_text(str(order.get("action") or "")),
                    symbol=self._strip(order.get("symbol")) or "-",
                    quantity=order.get("quantity") if order.get("quantity") is not None else "-",
                    price=self._format_price(order),
                    status=self._strip(order.get("status")) or "-",
                )
            )

        summary = self._strip(final_answer)
        if summary:
            lines.extend(["", "最终结论摘要:", self._truncate(summary, FINAL_ANSWER_MAX_CHARS)])
        return "\n".join(lines)

    def _format_price(self, order: Mapping[str, Any]) -> str:
        price_type = (self._strip(order.get("price_type")) or "MARKET").upper()
        price = order.get("price")
        if price_type == "MARKET" or price in (None, ""):
            return "市价"
        return f"{price_type} {price}"

    def _normalize_recipients(self, value: Any) -> list[str]:
        if isinstance(value, str):
            return [
                item.strip()
                for item in value.replace(";", ",").split(",")
                if item.strip()
            ]
        if isinstance(value, Sequence):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    def _action_text(self, action: str) -> str:
        normalized = action.upper()
        if normalized == "BUY":
            return "买入"
        if normalized == "SELL":
            return "卖出"
        return normalized or "交易"

    def _strip(self, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    def _truncate(self, text: str, max_chars: int) -> str:
        compact = " ".join(text.split())
        if len(compact) <= max_chars:
            return compact
        return compact[: max_chars - 3] + "..."


email_notification_service = EmailNotificationService()
