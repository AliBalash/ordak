from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _sqlite_connect_args(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


_engine: Engine | None = None
SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False)


def configure_database(database_url: str | None = None) -> Engine:
    global _engine
    resolved_url = database_url or settings.database_url
    _engine = create_engine(
        resolved_url,
        connect_args=_sqlite_connect_args(resolved_url),
        future=True,
    )
    SessionLocal.configure(bind=_engine)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        return configure_database()
    return _engine


def init_db() -> None:
    from app import models  # noqa: F401

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    if settings.database_url.startswith("sqlite"):
        _ensure_sqlite_schema(engine)


def _column_names(connection, table_name: str) -> set[str]:
    rows = connection.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def _ensure_column(connection, table_name: str, column_name: str, sql_type: str) -> None:
    columns = _column_names(connection, table_name)
    if column_name in columns:
        return
    connection.exec_driver_sql(
        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {sql_type}"
    )


def _loads_json_list(raw_value: str | None) -> list[object]:
    if not raw_value:
        return []
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _infer_provider_and_mode(
    *,
    provider: str | None,
    mode: str | None,
    answer: str | None,
    logs: str | None,
    uploads_json: str | None,
    output_images_json: str | None,
) -> tuple[str, str]:
    resolved_provider = provider or "gemini"
    answer_text = answer or ""
    logs_text = logs or ""
    if resolved_provider == "gemini" and ("ChatGPT" in answer_text or "ChatGPT" in logs_text):
        resolved_provider = "chatgpt"
    elif resolved_provider == "chatgpt" and ("Gemini" in answer_text or "Gemini" in logs_text):
        resolved_provider = "gemini"

    uploads = _loads_json_list(uploads_json)
    outputs = _loads_json_list(output_images_json)
    resolved_mode = mode or "chat"
    combined_text = f"{answer_text}\n{logs_text}"
    if outputs or "generated image output" in answer_text.lower() or "image mode" in logs_text.lower():
        resolved_mode = "image_generate"
    elif uploads and "analyze the uploaded image" in combined_text.lower():
        resolved_mode = "image_analyze"
    return resolved_provider, resolved_mode


def _ensure_sqlite_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        if "conversations" not in {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }:
            connection.exec_driver_sql(
                """
                CREATE TABLE conversations (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    title TEXT NOT NULL,
                    external_url TEXT,
                    external_conversation_id TEXT,
                    tab_window_id INTEGER,
                    tab_id INTEGER,
                    tab_window_key TEXT,
                    tab_target_id TEXT,
                    tab_alive BOOLEAN NOT NULL DEFAULT 0,
                    last_successful_job_id TEXT,
                    last_error_code TEXT,
                    pinned BOOLEAN NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        else:
            conversation_columns: dict[str, str] = {
                "external_url": "TEXT",
                "external_conversation_id": "TEXT",
                "tab_window_id": "INTEGER",
                "tab_id": "INTEGER",
                "tab_window_key": "TEXT",
                "tab_target_id": "TEXT",
                "tab_alive": "BOOLEAN DEFAULT 0",
                "last_successful_job_id": "TEXT",
                "last_error_code": "TEXT",
                "pinned": "BOOLEAN DEFAULT 0",
            }
            for column_name, sql_type in conversation_columns.items():
                _ensure_column(connection, "conversations", column_name, sql_type)

        job_columns: dict[str, str] = {
            "metadata_json": "TEXT",
            "provider": "TEXT DEFAULT 'gemini'",
            "conversation_id": "TEXT",
            "conversation_title": "TEXT",
            "mode": "TEXT DEFAULT 'chat'",
            "start_new_chat": "BOOLEAN DEFAULT 1",
            "error_code": "TEXT",
            "retry_of_job_id": "TEXT",
            "run_strategy": "TEXT",
            "agent_workspace": "TEXT",
            "agent_max_steps": "INTEGER",
            "agent_command_timeout_seconds": "INTEGER",
            "agent_execution_backend": "TEXT",
            "agent_network_enabled": "BOOLEAN",
            "recoverable": "BOOLEAN",
            "suggested_action": "TEXT",
            "cancel_requested_at": "DATETIME",
            "uploads_json": "TEXT",
            "output_images_json": "TEXT",
        }
        for column_name, sql_type in job_columns.items():
            _ensure_column(connection, "jobs", column_name, sql_type)

        if "agent_steps" not in {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }:
            connection.exec_driver_sql(
                """
                CREATE TABLE agent_steps (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    command_id TEXT NOT NULL,
                    tool TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT,
                    started_at DATETIME NOT NULL,
                    finished_at DATETIME,
                    duration_ms INTEGER,
                    error_message TEXT,
                    FOREIGN KEY(job_id) REFERENCES jobs (id) ON DELETE CASCADE
                )
                """
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX uq_agent_steps_job_sequence ON agent_steps(job_id, sequence)"
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX uq_agent_steps_job_command_id ON agent_steps(job_id, command_id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX ix_agent_steps_job_id ON agent_steps(job_id)"
            )

        rows = connection.exec_driver_sql(
            """
            SELECT id, question, created_at, metadata_json, provider, conversation_id, conversation_title,
                   mode, start_new_chat, uploads_json, output_images_json, answer, logs,
                   agent_workspace, agent_max_steps, agent_command_timeout_seconds,
                   agent_execution_backend, agent_network_enabled
            FROM jobs
            """
        ).fetchall()
        for row in rows:
            (
                job_id,
                question,
                created_at,
                metadata_json,
                provider,
                conversation_id,
                conversation_title,
                mode,
                start_new_chat,
                uploads_json,
                output_images_json,
                answer,
                logs,
                agent_workspace,
                agent_max_steps,
                agent_command_timeout_seconds,
                agent_execution_backend,
                agent_network_enabled,
            ) = row
            metadata_rows = _loads_json_list(metadata_json)
            metadata = metadata_rows[0] if metadata_rows else {}
            resolved_provider, resolved_mode = _infer_provider_and_mode(
                provider=provider or metadata.get("provider"),
                mode=mode or metadata.get("mode"),
                answer=answer,
                logs=logs,
                uploads_json=uploads_json or json.dumps(metadata.get("uploads", []), ensure_ascii=False),
                output_images_json=output_images_json or json.dumps(metadata.get("output_images", []), ensure_ascii=False),
            )
            resolved_conversation_id = conversation_id or metadata.get("conversation_id") or job_id
            resolved_title = (
                conversation_title
                or metadata.get("conversation_title")
                or " ".join((question or "").strip().split())[:52]
                or "New chat"
            )
            resolved_start_new = (
                start_new_chat
                if start_new_chat is not None
                else bool(metadata.get("start_new_chat", True))
            )
            resolved_uploads = uploads_json or json.dumps(metadata.get("uploads", []), ensure_ascii=False)
            resolved_outputs = output_images_json or json.dumps(
                metadata.get("output_images", []), ensure_ascii=False
            )
            resolved_agent_workspace = agent_workspace or metadata.get("agent_workspace")
            resolved_agent_max_steps = (
                agent_max_steps
                if agent_max_steps is not None
                else metadata.get("agent_max_steps")
            )
            resolved_agent_command_timeout_seconds = (
                agent_command_timeout_seconds
                if agent_command_timeout_seconds is not None
                else metadata.get("agent_command_timeout_seconds")
            )
            resolved_agent_execution_backend = (
                agent_execution_backend or metadata.get("agent_execution_backend")
            )
            resolved_agent_network_enabled = (
                agent_network_enabled
                if agent_network_enabled is not None
                else metadata.get("agent_network_enabled")
            )
            connection.exec_driver_sql(
                """
                UPDATE jobs
                SET provider = ?, conversation_id = ?, conversation_title = ?, mode = ?,
                    start_new_chat = ?, uploads_json = ?, output_images_json = ?,
                    agent_workspace = ?, agent_max_steps = ?, agent_command_timeout_seconds = ?,
                    agent_execution_backend = ?, agent_network_enabled = ?
                WHERE id = ?
                """,
                (
                    resolved_provider,
                    resolved_conversation_id,
                    resolved_title,
                    resolved_mode,
                    int(bool(resolved_start_new)),
                    resolved_uploads,
                    resolved_outputs,
                    resolved_agent_workspace,
                    resolved_agent_max_steps,
                    resolved_agent_command_timeout_seconds,
                    resolved_agent_execution_backend,
                    (
                        int(bool(resolved_agent_network_enabled))
                        if resolved_agent_network_enabled is not None
                        else None
                    ),
                    job_id,
                ),
            )
            conversation_exists = connection.exec_driver_sql(
                "SELECT 1 FROM conversations WHERE id = ?",
                (resolved_conversation_id,),
            ).fetchone()
            if conversation_exists:
                continue
            created_value = created_at or datetime.utcnow().isoformat()
            connection.exec_driver_sql(
                """
                INSERT INTO conversations (
                    id, provider, title, created_at, updated_at, tab_alive, pinned
                ) VALUES (?, ?, ?, ?, ?, 0, 0)
                """,
                (
                    resolved_conversation_id,
                    resolved_provider,
                    resolved_title,
                    created_value,
                    created_value,
                ),
            )

        conversation_rows = connection.exec_driver_sql(
            """
            SELECT c.id, c.title
            FROM conversations c
            """
        ).fetchall()
        for conversation_id, title in conversation_rows:
            latest_job = connection.exec_driver_sql(
                """
                SELECT provider, conversation_title
                FROM jobs
                WHERE conversation_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            if latest_job is None:
                continue
            latest_provider, latest_title = latest_job
            connection.exec_driver_sql(
                """
                UPDATE conversations
                SET provider = ?, title = COALESCE(?, title)
                WHERE id = ?
                """,
                (latest_provider or "gemini", latest_title or title, conversation_id),
            )


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


configure_database()
