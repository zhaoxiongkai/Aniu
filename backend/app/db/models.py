from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.constants import DEFAULT_SYSTEM_PROMPT


class Base(DeclarativeBase):
    pass


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_name: Mapped[str] = mapped_column(String(32), default="openai-compatible")
    mx_api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    llm_base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    llm_api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    llm_model: Mapped[str] = mapped_column(String(128), default="gpt-4o-mini")
    disabled_skill_ids_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )
    system_prompt: Mapped[str] = mapped_column(
        Text,
        default=DEFAULT_SYSTEM_PROMPT,
    )
    analyst_prompt: Mapped[str] = mapped_column(
        Text,
        default=(
            "请结合市场数据、资讯、候选股票、持仓和资金情况做判断。"
            "当信号不明确时返回HOLD。"
        ),
    )
    market_query: Mapped[str] = mapped_column(
        String(255), default="上证指数今天走势和市场概况"
    )
    news_query: Mapped[str] = mapped_column(String(255), default="今天A股市场热点新闻")
    screener_query: Mapped[str] = mapped_column(
        String(255), default="A股今天值得关注的强势股"
    )
    max_actions: Mapped[int] = mapped_column(Integer, default=2)
    trade_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_cash_only: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_max_order_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_max_daily_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    automation_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    automation_context_window_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=128000
    )
    automation_recent_message_limit: Mapped[int] = mapped_column(
        Integer, default=24
    )
    automation_enable_auto_compaction: Mapped[bool] = mapped_column(
        Boolean, default=True
    )
    automation_idle_summary_hours: Mapped[int] = mapped_column(Integer, default=12)
    automation_context_source: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default="default"
    )
    automation_context_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class StrategySchedule(Base):
    __tablename__ = "strategy_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), default="默认调度任务")
    run_type: Mapped[str] = mapped_column(String(32), default="analysis")
    interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    cron_expression: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retry_after_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class StrategyRun(Base):
    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger_source: Mapped[str] = mapped_column(String(32), default="manual")
    run_type: Mapped[str] = mapped_column(String(32), default="analysis")
    schedule_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    schedule_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    prompt_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_summary_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_tokens_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    analysis_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_request_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    llm_response_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    skill_payloads: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    decision_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    executed_actions: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    trade_orders: Mapped[list["TradeOrder"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )
    jev_assessments: Mapped[list["JevAssessment"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class TradeOrder(Base):
    __tablename__ = "trade_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("strategy_runs.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    action: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[int] = mapped_column(Integer)
    price_type: Mapped[str] = mapped_column(String(16), default="MARKET")
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="submitted")
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    run: Mapped[StrategyRun] = relationship(back_populates="trade_orders")


class TradeIntent(Base):
    __tablename__ = "trade_intents"
    __table_args__ = (UniqueConstraint("intent_key", name="uq_trade_intent_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("strategy_runs.id"), index=True)
    intent_key: Mapped[str] = mapped_column(String(128), nullable=False)
    trade_date: Mapped[str] = mapped_column(String(10), index=True)
    symbol: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    notional: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), default="reserved")
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ManualOrderGrant(Base):
    """A single exact order authorized by the local operator, never by a schedule."""

    __tablename__ = "manual_order_grants"
    __table_args__ = (UniqueConstraint("run_id", name="uq_manual_order_grant_run"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("strategy_runs.id"), nullable=False)
    trade_date: Mapped[str] = mapped_column(String(10), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    max_order_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    max_daily_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    intent_id: Mapped[int | None] = mapped_column(ForeignKey("trade_intents.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class JevAssessment(Base):
    __tablename__ = "jev_assessments"
    __table_args__ = (UniqueConstraint("intent_id", name="uq_jev_assessment_intent"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("strategy_runs.id"), index=True)
    intent_id: Mapped[int] = mapped_column(ForeignKey("trade_intents.id"), index=True)
    action: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(16))
    as_of: Mapped[str] = mapped_column(String(40))
    evidence_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    question_set: Mapped[str] = mapped_column(String(64))
    model_requested: Mapped[str] = mapped_column(String(64))
    model_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    probabilities: Mapped[dict[str, float] | None] = mapped_column(JSON, nullable=True)
    usage: Mapped[dict[str, int] | None] = mapped_column(JSON, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    run: Mapped[StrategyRun] = relationship(back_populates="jev_assessments")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120), default="新对话")
    kind: Mapped[str] = mapped_column(String(32), default="user", index=True)
    slug: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    archived_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_compacted_message_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    last_compacted_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )

    messages: Mapped[list["ChatMessageRecord"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessageRecord.id",
    )


class ChatMessageRecord(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    message_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    meta_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    attachments: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class ChatAttachment(Base):
    __tablename__ = "chat_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
