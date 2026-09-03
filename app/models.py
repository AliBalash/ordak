from __future__ import annotations

from datetime import datetime, timezone

import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Text, UniqueConstraint
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
    tab_window_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    tab_target_id: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    agent_workspace: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_max_steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_command_timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    agent_execution_backend: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_network_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
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
    output_videos_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    conversation: Mapped[Conversation | None] = relationship(back_populates="jobs")
    agent_steps: Mapped[list["AgentStep"]] = relationship(
        back_populates="job",
        order_by="AgentStep.sequence",
        cascade="all, delete-orphan",
    )


class AgentStep(Base):
    __tablename__ = "agent_steps"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="uq_agent_steps_job_sequence"),
        UniqueConstraint("job_id", "command_id", name="uq_agent_steps_job_command_id"),
    )

    id: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    job_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    command_id: Mapped[str] = mapped_column(Text, nullable=False)
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[Job] = relationship(back_populates="agent_steps")
