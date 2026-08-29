# -*- coding: utf-8 -*-
"""Оркестратор ИИ-анализа чатов.

Реализует принцип "скользящего окна с суммаризацией":
- Хранит один актуальный анализ (current_analys) на чат.
- При обновлении отправляет в LLM: старый анализ + новые сообщения.
- Получает новый анализ, старый сохраняет в историю.
- Вместо термина "summary" используется "analys".
"""

import logging
import time
from datetime import datetime
from typing import List, Optional

from . import prompts
from . import db_ops
from .llm_client import chat_completion

logger = logging.getLogger(__name__)


def _format_messages(messages: List[dict]) -> str:
    """Форматировать сообщения для отправки в LLM."""
    lines = []
    for m in messages:
        sender = m.get("sender_name") or m.get("sender_username") or "unknown"
        ts = m.get("create_time", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"[{time_str}] {sender}: {content}")
    return "\n".join(lines)


def _build_message_link(msg_id: int) -> str:
    """Создать текстовую ссылку на сообщение для LLM."""
    return f"[#msg:{msg_id}]"


def _build_chat_link(username: str) -> str:
    """Создать текстовую ссылку на чат для LLM."""
    return f"[chat:{username}]"


def run_analys(username: str) -> dict:
    """Запустить анализ одного чата.

    Args:
        username: Имя пользователя/чата.

    Returns:
        Словарь с результатами: {"success": bool, "analys": str, "error": str}
    """
    logger.info("Starting analys for %s", username)

    try:
        # 1. Получить текущий анализ (если есть)
        current = db_ops.get_chat_analys(username)
        current_analys = current["analys"] if current else None
        last_msg_id = db_ops.get_analys_last_msg_id(username)

        # 2. Получить новые сообщения
        new_msgs = db_ops.get_unprocessed_messages_since(
            username, last_msg_id=last_msg_id, limit=200
        )

        if not new_msgs and current_analys:
            # Нет новых сообщений, но анализ уже есть
            return {
                "success": True,
                "analys": current_analys,
                "new_count": 0,
                "message": "Нет новых сообщений для анализа",
            }

        if not new_msgs and not current_analys:
            # Нет сообщений вообще
            return {
                "success": False,
                "analys": "",
                "error": "Нет сообщений для анализа",
            }

        # 3. Формируем промпт
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        if current_analys:
            # Есть предыдущий анализ — обновляем
            system_prompt = prompts.SYSTEM_ANALYS_CHAT
            user_prompt = prompts.USER_ANALYS_TEMPLATE.format(
                date=now_str,
                current_analys=current_analys,
                new_messages=_format_messages(new_msgs),
            )
        else:
            # Первый анализ — все сообщения
            system_prompt = prompts.SYSTEM_FIRST_ANALYS
            user_prompt = prompts.USER_FIRST_ANALYS_TEMPLATE.format(
                messages=_format_messages(new_msgs),
            )

        # 4. Отправить в LLM
        llm_result = chat_completion(system_prompt, user_prompt)
        new_analys = llm_result["content"]
        token_usage = llm_result["usage"]

        # 5. Сохранить старый анализ в историю (если был)
        if current_analys:
            db_ops.save_analys_history(username, current_analys, 0)

        # 6. Сохранить новый анализ
        now_ts = int(time.time())
        last_id = new_msgs[-1]["id"] if new_msgs else (last_msg_id or 0)
        db_ops.set_chat_analys(username, new_analys, now_ts, last_id)

        # 7. Снять флаг новых сообщений
        db_ops.mark_has_new_messages(username, False)

        logger.info("Analys completed for %s (%d new messages)", username, len(new_msgs))

        return {
            "success": True,
            "analys": new_analys,
            "new_count": len(new_msgs),
            "updated_at": now_ts,
            "token_usage": token_usage,
        }

    except Exception as e:
        logger.error("Analys failed for %s: %s", username, e)
        return {"success": False, "analys": "", "error": str(e)}


def run_meta_analys() -> dict:
    """Запустить анализ всех анализов (мета-анализ).

    Returns:
        Словарь с результатами.
    """
    logger.info("Starting meta-analys")

    try:
        # 1. Собрать все текущие анализы
        all_analyses = db_ops.get_all_current_analyses()

        if not all_analyses:
            return {
                "success": False,
                "analys": "",
                "error": "Нет анализов для суммирования",
            }

        # 2. Форматировать для LLM
        analyses_text = []
        for item in all_analyses:
            display = item["alias"] or item["username"]
            chat_link = _build_chat_link(item["username"])
            analyses_text.append(
                f"--- Чат: {chat_link} ({display}) ---\n{item['analys']}"
            )

        full_text = "\n\n".join(analyses_text)

        # 3. Отправить в LLM
        llm_result = chat_completion(
            prompts.SYSTEM_META_ANALYS,
            prompts.USER_META_ANALYS_TEMPLATE.format(analyses=full_text),
        )
        meta_analys = llm_result["content"]
        token_usage = llm_result["usage"]

        # 4. Сохранить
        db_ops.save_meta_analys(meta_analys, len(all_analyses))

        logger.info("Meta-analys completed (%d chats)", len(all_analyses))

        return {
            "success": True,
            "analys": meta_analys,
            "chats_count": len(all_analyses),
            "token_usage": token_usage,
        }

    except Exception as e:
        logger.error("Meta-analys failed: %s", e)
        return {"success": False, "analys": "", "error": str(e)}


def get_analys_status(username: str) -> dict:
    """Получить статус анализа для чата.

    Returns:
        {"has_new_messages": bool, "has_analys": bool, "message_count": int}
    """
    from ..history_db import get_session

    session = get_session()
    try:
        r = session.query(
            ChatSetting.has_new_messages,
            ChatSetting.analys,
        ).filter_by(username=username).first()

        has_new = bool(r[0]) if r else False
        has_analys = bool(r[1]) if r else False
        total_msgs = db_ops.get_total_message_count(username)

        return {
            "has_new_messages": has_new,
            "has_analys": has_analys,
            "message_count": total_msgs,
        }
    finally:
        session.close()


def get_chat_analys(username: str) -> Optional[str]:
    """Получить анализ чата (через db_ops)."""
    result = db_ops.get_chat_analys(username)
    if result:
        return result["analys"]
    return None


def get_meta_analyses_history(limit: int = 20) -> List[dict]:
    """Получить историю мета-анализов."""
    return db_ops.get_meta_analyses_history(limit=limit)


# Импорт для get_analys_status
from ..history_db import ChatSetting