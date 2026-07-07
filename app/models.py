from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    external_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_conversation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    tab_window_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tab_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tab_alive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_successful_job_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    jobs: Mapped[list["Job"]] = relationship(back_populates="conversation")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    provider: Mapped[str] = mapped_column(Text, nullable=False, default="gemini", index=True)
    conversation_id: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("conversations.id"),
        nullable=True,
        index=True,
    )
    conversation_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(Text, nullable=False, default="chat")
    start_new_chat: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    retry_of_job_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    run_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    recoverable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    suggested_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    screenshot_paths: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploads_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_images_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    conversation: Mapped[Conversation | None] = relationship(back_populates="jobs")
