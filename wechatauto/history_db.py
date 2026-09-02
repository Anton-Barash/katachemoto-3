# -*- coding: utf-8 -*-
"""PostgreSQL база данных для хранения истории чатов и настроек.

Таблицы:
    - chat_settings: настройки чатов (alias, is_pinned, is_included, is_excluded)
    - sessions:      кэшированная информация о сессиях из WeChat
    - messages:      история сообщений
"""

from __future__ import annotations

import os
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from sqlalchemy import (
    Column, String, Boolean, Integer, BigInteger, Text,
    DateTime, UniqueConstraint, Index, create_engine, inspect, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")
DB_NAME = os.getenv("DB_NAME", "katachemoto")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

Base = declarative_base()


class ChatSetting(Base):
    """Настройки чата — заменяет config.json."""
    __tablename__ = "chat_settings"

    username = Column(String(255), primary_key=True)
    alias = Column(String(500), nullable=True)
    is_group = Column(Boolean, default=False)
    is_pinned = Column(Boolean, default=False)
    is_included = Column(Boolean, default=True)
    is_excluded = Column(Boolean, default=False)
    use_global_prompt = Column(Boolean, default=True)
    custom_prompt = Column(Text, nullable=True)
    tags = Column(String(255), nullable=True, default="")
    last_sync_time = Column(BigInteger, default=0)
    ai_analysis = Column(Text, nullable=True)
    ai_analysis_updated_at = Column(BigInteger, default=0)
    # Новые поля для модуля analyser
    has_new_messages = Column(Boolean, default=False)
    analys_last_msg_id = Column(BigInteger, nullable=True)
    analys = Column(Text, nullable=True)
    analys_updated_at = Column(BigInteger, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class GlobalSetting(Base):
    """Глобальные настройки — одна строка (id=1)."""
    __tablename__ = "global_settings"

    id = Column(Integer, primary_key=True, default=1)
    global_prompt = Column(Text, nullable=False,
                           default="сделай краткий пересказ. Выдели задачи и проблемы. Ответ на русском. Какие у кокго задачи?")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class AiAnalysis(Base):
    """История AI-анализов для каждого чата."""
    __tablename__ = "ai_analyses"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(255), nullable=False, index=True)
    analysis = Column(Text, nullable=False)
    message_count = Column(Integer, default=0)
    prompt_used = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MetaAnalysis(Base):
    """Мета-анализ — анализ всех анализов чатов."""
    __tablename__ = "meta_analyses"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    analysis = Column(Text, nullable=False)
    chats_analyzed = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Session(Base):
    """Кэшированная информация о сессиях из WeChat."""
    __tablename__ = "sessions"

    username = Column(String(255), primary_key=True)
    nickname = Column(String(500), nullable=True)
    display_name = Column(String(500), nullable=True)
    is_group = Column(Boolean, default=False)
    last_message_text = Column(Text, nullable=True)
    last_message_time = Column(BigInteger, default=0)
    unread_count = Column(Integer, default=0)
    summary = Column(Text, nullable=True)
    first_seen_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))


class Message(Base):
    """История сообщений."""
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("username", "local_id", name="uq_messages_username_local_id"),
        Index("idx_messages_username", "username"),
        Index("idx_messages_create_time", "create_time"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(255), nullable=False)
    local_id = Column(BigInteger, nullable=True)
    sender_username = Column(String(255), nullable=True)
    sender_name = Column(String(500), nullable=True)
    content = Column(Text, nullable=True)
    media_path = Column(Text, nullable=True)
    msg_type = Column(String(50), nullable=True)
    create_time = Column(BigInteger, nullable=True)
    is_self = Column(Boolean, default=False)
    ai_processed = Column(Boolean, default=False)
    quote_content = Column(Text, nullable=True)
    quote_sender = Column(String(255), nullable=True)
    quote_display = Column(String(500), nullable=True)
    quote_local_id = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5)
    return _engine


def get_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


def _migrate_ai_analyses_constraints(engine):
    """Удалить лишнее уникальное ограничение на username в таблице ai_analyses."""
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT 1 FROM pg_constraint "
                    "WHERE conrelid = 'ai_analyses'::regclass "
                    "AND conname = 'unique_ai_analysis_username'"
                )
            ).fetchone()
            if row:
                conn.execute(text('ALTER TABLE ai_analyses DROP CONSTRAINT "unique_ai_analysis_username"'))
                conn.commit()
                logging.info("Удалено уникальное ограничение unique_ai_analysis_username из таблицы ai_analyses")
    except Exception as e:
        logging.warning("Не удалось удалить ограничение unique_ai_analysis_username: %s", e)


def _migrate_ai_analyses_prompt_column(engine):
    """Добавить колонку prompt_used в таблицу ai_analyses, если она отсутствует."""
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("ai_analyses")}
        if "prompt_used" not in columns:
            with engine.connect() as conn:
                conn.execute(text(f'ALTER TABLE ai_analyses ADD COLUMN prompt_used TEXT'))
                conn.commit()
                logging.info("Добавлена колонка prompt_used в таблицу ai_analyses")
    except Exception as e:
        logging.warning("Не удалось добавить колонку prompt_used: %s", e)




def init_db():
    """Создать таблицы, если их нет, и выполнить миграцию колонок."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_chat_settings_columns(engine)
    _migrate_messages_columns(engine)
    _migrate_ai_analyses_constraints(engine)
    _migrate_ai_analyses_prompt_column(engine)
    logging.info("PostgreSQL tables created/verified")


def _migrate_chat_settings_columns(engine):
    """Добавить недостающие колонки в chat_settings (миграция без потери данных)."""
    inspector = inspect(engine)
    existing_columns = {c["name"] for c in inspector.get_columns("chat_settings")}

    new_columns = {
        "has_new_messages": "BOOLEAN DEFAULT FALSE",
        "analys_last_msg_id": "BIGINT",
        "analys": "TEXT",
        "analys_updated_at": "BIGINT DEFAULT 0",
        "tags": "VARCHAR(255) DEFAULT ''",
    }

    added = 0
    with engine.connect() as conn:
        for col_name, col_def in new_columns.items():
            if col_name not in existing_columns:
                try:
                    conn.execute(
                        text(f'ALTER TABLE chat_settings ADD COLUMN "{col_name}" {col_def}')
                    )
                    added += 1
                except Exception as e:
                    logging.warning("Migration failed for column %s: %s", col_name, e)
        if added:
            conn.commit()
            logging.info("Migrated %d new columns to chat_settings", added)


def _migrate_messages_columns(engine):
    """Добавить недостающие колонки в messages (миграция без потери данных)."""
    inspector = inspect(engine)
    existing_columns = {c["name"] for c in inspector.get_columns("messages")}

    new_columns = {
        "media_path": "TEXT",
        "quote_content": "TEXT",
        "quote_sender": "VARCHAR(255)",
        "quote_display": "VARCHAR(500)",
        "quote_local_id": "BIGINT",
    }

    with engine.connect() as conn:
        for col_name, col_def in new_columns.items():
            if col_name not in existing_columns:
                try:
                    conn.execute(
                        text(f'ALTER TABLE messages ADD COLUMN "{col_name}" {col_def}')
                    )
                    logging.info("Migrated column %s to messages", col_name)
                except Exception as e:
                    logging.warning("Migration failed for column %s: %s", col_name, e)
        conn.commit()


def get_all_settings() -> Dict[str, dict]:
    """Загрузить все настройки чатов из БД.

    Возвращает словарь {username: {alias, is_group, is_pinned, is_included, is_excluded,
                                   use_global_prompt, custom_prompt, has_new_messages, analys}}
    """
    session = get_session()
    try:
        rows = session.query(ChatSetting).all()
        result = {}
        for r in rows:
            result[r.username] = {
                "alias": r.alias,
                "is_group": r.is_group,
                "is_pinned": r.is_pinned,
                "is_included": r.is_included,
                "is_excluded": r.is_excluded,
                "use_global_prompt": r.use_global_prompt,
                "custom_prompt": r.custom_prompt,
                "last_sync_time": r.last_sync_time,
                "ai_analysis": r.ai_analysis,
                "ai_analysis_updated_at": r.ai_analysis_updated_at,
                "has_new_messages": r.has_new_messages,
                "analys": r.analys,
                "analys_updated_at": r.analys_updated_at,
                "tags": r.tags or "",
            }
        return result
    finally:
        session.close()


def get_setting(username: str) -> Optional[dict]:
    """Получить настройки одного чата."""
    session = get_session()
    try:
        r = session.query(ChatSetting).filter_by(username=username).first()
        if not r:
            return None
        return {
            "alias": r.alias,
            "is_group": r.is_group,
            "is_pinned": r.is_pinned,
            "is_included": r.is_included,
            "is_excluded": r.is_excluded,
            "use_global_prompt": r.use_global_prompt,
            "custom_prompt": r.custom_prompt,
            "last_sync_time": r.last_sync_time,
            "ai_analysis": r.ai_analysis,
            "ai_analysis_updated_at": r.ai_analysis_updated_at,
            "has_new_messages": r.has_new_messages,
            "analys": r.analys,
            "analys_updated_at": r.analys_updated_at,
            "tags": r.tags or "",
        }
    finally:
        session.close()


def upsert_setting(username: str, **kwargs) -> None:
    """Создать или обновить настройки чата.

    kwargs: alias, is_pinned, is_included, is_excluded
    """
    session = get_session()
    try:
        existing = session.query(ChatSetting).filter_by(username=username).first()
        if existing:
            for key, val in kwargs.items():
                if hasattr(existing, key) and val is not None:
                    setattr(existing, key, val)
            existing.updated_at = datetime.now(timezone.utc)
        else:
            data = {"username": username}
            data.update(kwargs)
            session.add(ChatSetting(**data))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def set_alias(username: str, alias: str) -> None:
    """Установить alias для чата. Если alias пустой — удалить."""
    if alias:
        upsert_setting(username, alias=alias)
    else:
        session = get_session()
        try:
            r = session.query(ChatSetting).filter_by(username=username).first()
            if r:
                r.alias = None
                r.updated_at = datetime.now(timezone.utc)
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def set_pinned(username: str, is_pinned: bool) -> None:
    """Закрепить/открепить чат."""
    upsert_setting(username, is_pinned=is_pinned)


def get_pinned_list() -> List[str]:
    """Получить список закреплённых чатов."""
    session = get_session()
    try:
        rows = session.query(ChatSetting.username).filter_by(is_pinned=True).all()
        return [r[0] for r in rows]
    finally:
        session.close()


def get_aliases() -> Dict[str, str]:
    """Получить словарь {username: alias}."""
    session = get_session()
    try:
        rows = session.query(ChatSetting).filter(
            ChatSetting.alias.isnot(None),
            ChatSetting.alias != ""
        ).all()
        return {r.username: r.alias for r in rows}
    finally:
        session.close()


def save_message(username: str, local_id: int, sender: str = "",
                 sender_name: str = "", content: str = "",
                 media_path: str = "",
                 msg_type: str = "", create_time: int = 0,
                 is_self: bool = False,
                 quote_content: str = "", quote_sender: str = "",
                 quote_display: str = "", quote_local_id: int = None) -> None:
    """Сохранить одно сообщение в историю."""
    session = get_session()
    try:
        existing = session.query(Message).filter_by(
            username=username, local_id=local_id
        ).first()
        if existing:
            return
        msg = Message(
            username=username,
            local_id=local_id,
            sender_username=sender,
            sender_name=sender_name,
            content=content,
            media_path=media_path,
            msg_type=msg_type,
            create_time=create_time,
            is_self=is_self,
            quote_content=quote_content or None,
            quote_sender=quote_sender or None,
            quote_display=quote_display or None,
            quote_local_id=quote_local_id,
        )
        session.add(msg)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_messages(username: str, limit: int = 100, offset: int = 0) -> List[dict]:
    """Получить историю сообщений из PostgreSQL."""
    session = get_session()
    try:
        rows = (
            session.query(Message)
            .filter_by(username=username)
            .order_by(Message.create_time.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        result = []
        for r in rows:
            result.append({
                "id": r.id,
                "local_id": r.local_id,
                "sender_username": r.sender_username,
                "sender_name": r.sender_name,
                "content": r.content,
                "media_path": r.media_path,
                "msg_type": r.msg_type,
                "create_time": r.create_time,
                "is_self": r.is_self,
                "quote_content": r.quote_content,
                "quote_sender": r.quote_sender,
                "quote_display": r.quote_display,
                "quote_local_id": r.quote_local_id,
            })
        return result
    finally:
        session.close()


def get_pg_message_count(username: str) -> int:
    """Сколько сообщений в PostgreSQL для данного чата."""
    session = get_session()
    try:
        return session.query(Message).filter_by(username=username).count()
    finally:
        session.close()


# ─── Prompt settings ────────────────────────────────────────────────

DEFAULT_GLOBAL_PROMPT = "сделай краткий пересказ. Выдели задачи и проблемы. Ответ на русском. Какие у кокго задачи?"


def get_global_prompt() -> str:
    """Получить глобальный промт."""
    session = get_session()
    try:
        gs = session.query(GlobalSetting).filter_by(id=1).first()
        if gs and gs.global_prompt:
            return gs.global_prompt
        return DEFAULT_GLOBAL_PROMPT
    finally:
        session.close()


def set_global_prompt(prompt: str) -> None:
    """Обновить глобальный промт."""
    session = get_session()
    try:
        gs = session.query(GlobalSetting).filter_by(id=1).first()
        if gs:
            gs.global_prompt = prompt
            gs.updated_at = datetime.now(timezone.utc)
        else:
            session.add(GlobalSetting(id=1, global_prompt=prompt))
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_effective_prompt(username: str) -> str:
    """Получить эффективный промт для чата (глобальный или индивидуальный)."""
    session = get_session()
    try:
        r = session.query(ChatSetting).filter_by(username=username).first()
        if r and not r.use_global_prompt and r.custom_prompt:
            return r.custom_prompt
        return get_global_prompt()
    finally:
        session.close()


# ─── Sync / AI processing helpers ───────────────────────────────────


def get_last_sync_time(username: str) -> int:
    """Получить время последней синхронизации сообщений (Unix timestamp)."""
    session = get_session()
    try:
        r = session.query(ChatSetting.last_sync_time).filter_by(username=username).first()
        return r[0] if r and r[0] else 0
    finally:
        session.close()


def set_last_sync_time(username: str, timestamp: int) -> None:
    """Обновить время последней синхронизации."""
    upsert_setting(username, last_sync_time=timestamp)


def get_unprocessed_messages(username: str, limit: int = 100) -> List[dict]:
    """Получить сообщения, ещё не обработанные ИИ."""
    session = get_session()
    try:
        rows = (
            session.query(Message)
            .filter_by(username=username, ai_processed=False)
            .order_by(Message.create_time.asc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "local_id": r.local_id,
                "sender_username": r.sender_username,
                "sender_name": r.sender_name,
                "content": r.content,
                "msg_type": r.msg_type,
                "create_time": r.create_time,
                "is_self": r.is_self,
            }
            for r in rows
        ]
    finally:
        session.close()


def mark_messages_processed(message_ids: List[int]) -> int:
    """Пометить сообщения как обработанные ИИ. Возвращает количество обновлённых."""
    if not message_ids:
        return 0
    session = get_session()
    try:
        count = (
            session.query(Message)
            .filter(Message.id.in_(message_ids))
            .update({Message.ai_processed: True}, synchronize_session=False)
        )
        session.commit()
        return count
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_message_stats(username: str) -> dict:
    """Статистика сообщений в PG для чата: всего, обработано, не обработано."""
    session = get_session()
    try:
        total = session.query(Message).filter_by(username=username).count()
        processed = session.query(Message).filter_by(username=username, ai_processed=True).count()
        unprocessed = total - processed
        return {"total": total, "processed": processed, "unprocessed": unprocessed}
    finally:
        session.close()


def set_ai_analysis(username: str, analysis: str, timestamp: int = 0,
                     message_count: int = 0, prompt_used: str = "") -> None:
    """Сохранить AI-анализ для чата (в chat_settings + в историю ai_analyses)."""
    # Обновить latest в chat_settings
    upsert_setting(username, ai_analysis=analysis, ai_analysis_updated_at=timestamp)

    # Сохранить в историю
    session = get_session()
    try:
        record = AiAnalysis(
            username=username,
            analysis=analysis,
            message_count=message_count,
            prompt_used=prompt_used or None,
        )
        session.add(record)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_ai_analysis(username: str) -> Optional[str]:
    """Получить последний AI-анализ для чата."""
    session = get_session()
    try:
        r = session.query(ChatSetting.ai_analysis).filter_by(username=username).first()
        return r[0] if r else None
    finally:
        session.close()


def get_ai_analysis_history(username: str, limit: int = 50) -> List[dict]:
    """Получить историю AI-анализов для чата (от новых к старым)."""
    session = get_session()
    try:
        rows = (
            session.query(AiAnalysis)
            .filter_by(username=username)
            .order_by(AiAnalysis.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "analysis": r.analysis,
                "message_count": r.message_count,
                "prompt_used": r.prompt_used,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
    finally:
        session.close()


def get_ai_analysis_count(username: str) -> int:
    """Сколько раз делали AI-анализ для чата."""
    session = get_session()
    try:
        return session.query(AiAnalysis).filter_by(username=username).count()
    finally:
        session.close()


def unpin_and_cleanup(username: str) -> dict:
    """Открепить чат и удалить историю сообщений, оставив AI-анализ.

    Возвращает словарь с количеством удалённых сообщений.
    """
    session = get_session()
    try:
        # Удалить все сообщения чата
        deleted = session.query(Message).filter_by(username=username).delete()

        # Сбросить настройки sync, но оставить ai_analysis
        r = session.query(ChatSetting).filter_by(username=username).first()
        if r:
            r.is_pinned = False
            r.last_sync_time = 0

        session.commit()
        return {"deleted": deleted}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()