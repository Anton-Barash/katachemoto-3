# -*- coding: utf-8 -*-
"""Операции с базой данных для модуля analyser."""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from ..history_db import (
    get_session,
    ChatSetting,
    AiAnalysis,
    Message,
    upsert_setting,
)

logger = logging.getLogger(__name__)


def get_unprocessed_messages_since(
    username: str,
    last_msg_id: Optional[int] = None,
    limit: int = 200,
) -> List[dict]:
    """Получить необработанные сообщения для анализа.

    Args:
        username: Имя чата.
        last_msg_id: ID последнего проанализированного сообщения.
        limit: Максимум сообщений.

    Returns:
        Список словарей сообщений.
    """
    session = get_session()
    try:
        query = (
            session.query(Message)
            .filter_by(username=username)
            .order_by(Message.create_time.asc())
        )
        if last_msg_id is not None:
            query = query.filter(Message.id > last_msg_id)
        rows = query.limit(limit).all()
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
                "quote_content": r.quote_content,
                "quote_sender": r.quote_sender,
                "quote_display": r.quote_display,
            }
            for r in rows
        ]
    finally:
        session.close()


def get_messages_before(
    username: str,
    last_msg_id: Optional[int] = None,
    limit: int = 200,
) -> List[dict]:
    """Получить последние N сообщений ДО указанного ID (включая его).

    Используется для повторного анализа: возвращает то же окно сообщений,
    которое было в последнем анализе (id <= last_msg_id).
    """
    session = get_session()
    try:
        query = (
            session.query(Message)
            .filter_by(username=username)
            .order_by(Message.id.desc())
        )
        if last_msg_id is not None:
            query = query.filter(Message.id <= last_msg_id)
        rows = query.limit(limit).all()
        rows.reverse()  # от старых к новым
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
                "quote_content": r.quote_content,
                "quote_sender": r.quote_sender,
                "quote_display": r.quote_display,
            }
            for r in rows
        ]
    finally:
        session.close()


_nick_index_cache: Optional[Dict[str, str]] = None


def get_chat_display_name(username: str) -> str:
    """Получить отображаемое имя чата: alias из PostgreSQL (chat_settings),
    затем nickname из WeChat contact.db, затем сам username."""
    session = get_session()
    try:
        r = (
            session.query(ChatSetting.alias)
            .filter_by(username=username)
            .first()
        )
        if r and r[0]:
            return r[0]
    finally:
        session.close()

    try:
        nick_index = get_nickname_index()
        name = nick_index.get(username)
        if name:
            return name
    except Exception:
        pass

    return username


def get_nickname_index() -> Dict[str, str]:
    """Лениво получить индекс никнеймов (wxid -> имя) из WeChatDB.

    Кэшируется на всё время жизни процесса. Если WeChatDB недоступна,
    возвращается пустой словарь — анализ работает по username.
    """
    global _nick_index_cache
    if _nick_index_cache is not None:
        return _nick_index_cache
    try:
        from .. import WeChatDB

        db = WeChatDB()
        _nick_index_cache = db.get_nickname_index()
    except Exception as e:
        logger.warning("nickname index unavailable: %s", e)
        _nick_index_cache = {}
    return _nick_index_cache


def get_analys_last_msg_id(username: str) -> Optional[int]:
    """Получить ID последнего проанализированного сообщения."""
    session = get_session()
    try:
        r = (
            session.query(ChatSetting.analys_last_msg_id)
            .filter_by(username=username)
            .first()
        )
        return r[0] if r and r[0] else None
    finally:
        session.close()


def set_analys_last_msg_id(username: str, msg_id: int) -> None:
    """Установить ID последнего проанализированного сообщения."""
    upsert_setting(username, analys_last_msg_id=msg_id)


def mark_has_new_messages(username: str, has_new: bool = True) -> None:
    """Установить флаг наличия новых непроанализированных сообщений."""
    upsert_setting(username, has_new_messages=has_new)


def get_chats_with_new_messages() -> List[str]:
    """Получить список чатов с флагом has_new_messages=True."""
    session = get_session()
    try:
        rows = (
            session.query(ChatSetting.username)
            .filter_by(has_new_messages=True)
            .all()
        )
        return [r[0] for r in rows]
    finally:
        session.close()


def get_chat_analys(username: str) -> Optional[dict]:
    """Получить последний анализ чата."""
    session = get_session()
    try:
        r = (
            session.query(ChatSetting.analys, ChatSetting.analys_updated_at)
            .filter_by(username=username)
            .first()
        )
        if r and r[0]:
            return {
                "analys": r[0],
                "updated_at": r[1],
            }
        return None
    finally:
        session.close()


def set_chat_analys(
    username: str,
    analys: str,
    timestamp: int,
    last_msg_id: int,
) -> None:
    """Сохранить анализ чата и обновить метаданные."""
    upsert_setting(
        username,
        analys=analys,
        analys_updated_at=timestamp,
        analys_last_msg_id=last_msg_id,
        has_new_messages=False,
    )


def save_analys_history(username: str, analys: str, message_count: int, prompt_used: str = None) -> None:
    """Сохранить версию анализа в историю."""
    session = get_session()
    try:
        from sqlalchemy.exc import IntegrityError
        record = AiAnalysis(
            username=username,
            analysis=analys,
            message_count=message_count,
            prompt_used=prompt_used,
        )
        session.add(record)
        session.commit()
    except IntegrityError as e:
        session.rollback()
        # Игнорируем дублирование записей истории анализов
        if "unique_ai_analysis_username" not in str(e):
            raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def save_meta_analys(analys: str, chats_count: int) -> None:
    """Сохранить мета-анализ (анализ анализов)."""
    session = get_session()
    try:
        from ..history_db import MetaAnalysis
        record = MetaAnalysis(
            analysis=analys,
            chats_analyzed=chats_count,
        )
        session.add(record)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_last_meta_analys() -> Optional[dict]:
    """Получить последний сохранённый общий анализ (мета-анализ)."""
    session = get_session()
    try:
        from ..history_db import MetaAnalysis
        row = (
            session.query(MetaAnalysis)
            .order_by(MetaAnalysis.created_at.desc())
            .first()
        )
        if row:
            return {
                "analysis": row.analysis,
                "chats_analyzed": row.chats_analyzed,
                "created_at": row.created_at,
            }
        return None
    finally:
        session.close()


def get_recent_analyses(username: str, limit: int = 3, exclude_current: bool = True) -> List[dict]:
    """Получить N последних предыдущих анализов для чата.
    
    Args:
        username: Имя чата/пользователя
        limit: Максимальное количество возвращаемых записей
        exclude_current: Исключать ли текущий актуальный анализ из истории
    """
    session = get_session()
    try:
        # Получаем ID текущего анализа, если нужно исключить его
        current_analys_id = None
        if exclude_current:
            current = get_chat_analys(username)
            if current and "id" in current:
                current_analys_id = current["id"]
        
        # Получаем последние N анализов
        query = (
            session.query(AiAnalysis)
            .filter_by(username=username)
            .order_by(AiAnalysis.created_at.desc())
        )
        if current_analys_id is not None:
            query = query.filter(AiAnalysis.id != current_analys_id)
        
        rows = query.limit(limit).all()
        return [
            {
                "id": r.id,
                "analysis": r.analysis,
                "created_at": r.created_at,
                "message_count": r.message_count,
                "prompt_used": r.prompt_used,
            }
            for r in rows
        ]
    finally:
        session.close()


def get_meta_analyses_history(limit: int = 20) -> List[dict]:
    """Получить историю мета-анализов."""
    session = get_session()
    try:
        from ..history_db import MetaAnalysis

        rows = (
            session.query(MetaAnalysis)
            .order_by(MetaAnalysis.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": r.id,
                "analysis": r.analysis,
                "chats_analyzed": r.chats_analyzed,
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in rows
        ]
    finally:
        session.close()


def get_all_current_analyses() -> List[dict]:
    """Получить все текущие анализы чатов."""
    session = get_session()
    try:
        rows = (
            session.query(ChatSetting.username, ChatSetting.analys, ChatSetting.alias)
            .filter(ChatSetting.analys.isnot(None))
            .filter(ChatSetting.analys != "")
            .all()
        )
        return [
            {
                "username": r[0],
                "analys": r[1],
                "alias": r[2],
            }
            for r in rows
        ]
    finally:
        session.close()


def get_total_message_count(username: str) -> int:
    """Получить общее количество сообщений в чате."""
    session = get_session()
    try:
        return session.query(Message).filter_by(username=username).count()
    finally:
        session.close()