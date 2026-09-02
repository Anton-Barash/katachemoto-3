# -*- coding: utf-8 -*-
"""Оркестратор ИИ-анализа чатов.

Реализует принцип "скользящего окна с суммаризацией":
- Хранит один актуальный анализ (current_analys) на чат.
- При обновлении отправляет в LLM: старый анализ + новые сообщения.
- Получает новый анализ, старый сохраняет в историю.
- Вместо термина "summary" используется "analys".
"""

import logging
import re
import time
from datetime import datetime
from typing import List, Optional

from . import prompts
from . import db_ops
from .llm_client import chat_completion

logger = logging.getLogger(__name__)


def _format_messages(messages: List[dict], username: str = "") -> str:
    """Форматировать сообщения для отправки в LLM.

    Включает: время, никнейм (resolved), текст, цитирования/ответы,
    маркеры медиа-сообщений. В группах вычищает префикс "wxid_xxx: " из контента.
    """
    nick_index = db_ops.get_nickname_index() if "@chatroom" in username else {}
    is_group = "@chatroom" in username

    # Для личного чата имя собеседника — имя самого чата (alias из chat_settings),
    # т.к. sender_username в личных чатах может быть внутренним WeChat-алиасом
    # с неверным отображаемым именем в contact.db.
    chat_display = db_ops.get_chat_display_name(username) if not is_group else username

    def _display(wxid: str) -> str:
        return nick_index.get(wxid, wxid) if wxid else (wxid or "unknown")

    lines = []
    for m in messages:
        sender_id = m.get("sender_username") or ""
        if m.get("is_self"):
            sender = "Я"
        elif is_group:
            sender = m.get("sender_name") or _display(sender_id)
        else:
            sender = m.get("sender_name") or chat_display
        ts = m.get("create_time", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?"

        content = (m.get("content") or "").strip()

        # Служебные сообщения о звонках (voipmsg) не несут смысловой
        # нагрузки для анализа переписки — полностью исключаем их.
        if "<voipmsg" in content.lower():
            continue

        # В группах WeChat хранит "wxid_xxx:\nтекст" — вычищаем префикс
        if is_group and content:
            mm = re.match(r"^(wxid_[A-Za-z0-9_\-]+|[^:\n]{1,40}):\s*\n?", content)
            if mm:
                content = content[mm.end():].strip()

        msg_type = (m.get("msg_type") or "").strip()
        is_text = not msg_type or msg_type in ("文本", "text")

        # Цитирование / ответ
        quote_parts = []
        q_sender = m.get("quote_display") or m.get("quote_sender")
        q_content = (m.get("quote_content") or "").strip()
        if q_sender or q_content:
            if is_group:
                q_name = _display(q_sender) if q_sender else "unknown"
            else:
                # В личном чате автор цитаты — либо собеседник, либо Я
                q_name = chat_display
            quote_parts.append(
                f"(в ответ на {q_name}: {q_content or '[цитата]'})"
            )

        # Медиа вместо сырого контента
        if not content and not is_text:
            content = f"[{msg_type}]"

        if content or quote_parts:
            local_id = m.get("local_id")
            marker = f" [#msg:{local_id}]" if local_id else ""
            line = f"[{time_str}] {sender}: {' '.join(quote_parts)} {content}{marker}".strip()
            lines.append(line)
    return "\n".join(lines)


def _build_message_link(msg_id: int) -> str:
    """Создать текстовую ссылку на сообщение для LLM."""
    return f"[#msg:{msg_id}]"


def _build_chat_link(username: str) -> str:
    """Создать текстовую ссылку на чат для LLM."""
    return f"[chat:{username}]"


def run_analys(username: str, force: bool = False) -> dict:
    """Запустить анализ одного чата.

    Args:
        username: Имя пользователя/чата.
        force: Если True, принудительно запустить анализ даже если нет новых сообщений.

    Returns:
        Словарь с результатами: {"success": bool, "analys": str, "error": str}
    """
    logger.info("Starting analys for %s (force=%s)", username, force)

    try:
        # 1. Получить текущий анализ (если есть)
        current = db_ops.get_chat_analys(username)
        current_analys = current["analys"] if current else None

        # 2. Получаем сообщения для анализа
        last_msg_id = db_ops.get_analys_last_msg_id(username)

        if force:
            # «Переделать»: всегда то ЖЕ окно сообщений, что было в последнем
            # анализе (id <= last_msg_id). Новые сообщения, пришедшие после,
            # игнорируются — их обработает обычный «Проанализировать».
            new_msgs = db_ops.get_messages_before(username, last_msg_id=last_msg_id, limit=200)
        else:
            new_msgs = db_ops.get_unprocessed_messages_since(username, last_msg_id=last_msg_id, limit=200)

        if not new_msgs and not current_analys:
            return {
                "success": False,
                "error": "Нет сообщений для анализа",
            }

        if not new_msgs and current_analys and not force:
            # Нет новых сообщений и не принудительный запуск
            return {
                "success": True,
                "analys": current_analys,
                "new_count": 0,
                "message": "Нет новых сообщений для анализа",
            }

        # 3. Формируем промпт
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        if force and current_analys:
            # «Переделать»: текущий (неверный) анализ НЕ добавляем в промпт,
            # чтобы не усиливать ошибку. Берём только предыдущие анализы из
            # истории (если есть) + то же окно сообщений + изменённый промпт.
            system_prompt = prompts.SYSTEM_FIRST_ANALYS

            # Последние 3 анализа из истории (до текущего)
            recent_analyses = db_ops.get_recent_analyses(username, limit=3)
            if recent_analyses:
                recent_sections = []
                for i, ana in enumerate(recent_analyses, 1):
                    ana_date = ana["created_at"].strftime("%Y-%m-%d %H:%M") if ana["created_at"] else "?"
                    recent_sections.append(
                        f"=== Старый анализ #{i} (дата: {ana_date}) ===\n{ana['analysis']}"
                    )
                user_prompt = (
                    "--- Предыдущие анализы (контекст, не переписывай их целиком) ---\n"
                    + "\n\n".join(recent_sections)
                    + "\n\n=== Сообщения чата ===\n"
                    + _format_messages(new_msgs, username)
                    + "\n\nСоставь новый структурированный анализ. Перенеси все "
                      "актуальные задачи из предыдущих анализов, если они ещё "
                      "не закрыты по сообщениям."
                )
            else:
                user_prompt = prompts.USER_FIRST_ANALYS_TEMPLATE.format(
                    messages=_format_messages(new_msgs, username),
                )
        elif current_analys:
            # Есть предыдущий анализ — обновляем
            system_prompt = prompts.SYSTEM_ANALYS_CHAT
            
            # Получаем последние 3 предыдущих анализов
            recent_analyses = db_ops.get_recent_analyses(username, limit=3)
            recent_analyses_text = []
            
            for i, ana in enumerate(recent_analyses, 1):
                ana_date = ana["created_at"].strftime("%Y-%m-%d %H:%M") if ana["created_at"] else "?"
                recent_analyses_text.append(
                    f"=== Старый анализ #{i} (дата: {ana_date}) ===\n{ana['analysis']}"
                )
            
            # Собираем полный промпт
            user_prompt_sections = []
            if recent_analyses_text:
                user_prompt_sections.append("\n".join(recent_analyses_text))
            user_prompt_sections.append(f"=== Текущий анализ на дату {now_str} ===\n{current_analys}")
            user_prompt_sections.append(f"\n=== Новые сообщения ===\n{_format_messages(new_msgs, username)}")
            user_prompt_sections.append("\nОбнови анализ согласно правилам. ВАЖНО: Перенеси все актуальные задачи из всех предыдущих анализов в новый анализ, чтобы они не потерялись.")
            
            user_prompt = "\n\n".join(user_prompt_sections)
        else:
            # Первый анализ — все сообщения
            system_prompt = prompts.SYSTEM_FIRST_ANALYS
            user_prompt = prompts.USER_FIRST_ANALYS_TEMPLATE.format(
                messages=_format_messages(new_msgs, username),
            )

        # 3.1 Добавить промпт пользователя из БД (приоритет в оформлении ответа)
        from ..history_db import get_effective_prompt

        user_instruction = get_effective_prompt(username)
        if user_instruction:
            system_prompt += (
                "\n\n=== ТРЕБОВАНИЯ ПОЛЬЗОВАТЕЛЯ К ФОРМАТУ И СОДЕРЖАНИЮ ===\n"
                "Эти требования имеют НАИВЫСШИЙ приоритет при оформлении ответа. "
                "Соблюдай их строго:\n"
                f"{user_instruction}\n"
                "=== КОНЕЦ ТРЕБОВАНИЙ ==="
            )

        # 4. Отправить в LLM
        llm_result = chat_completion(system_prompt, user_prompt)
        new_analys = llm_result["content"]
        token_usage = llm_result["usage"]

        # 5. Сохранить старый анализ в историю (если был) — без промта,
        # т.к. фактический промт старого анализа уже сохранён при его создании.
        if current_analys:
            db_ops.save_analys_history(username, current_analys, 0)

        # 6. Сохранить новый анализ
        now_ts = int(time.time())
        last_id = new_msgs[-1]["id"] if new_msgs else (last_msg_id or 0)
        db_ops.set_chat_analys(username, new_analys, now_ts, last_id)
        
        # Сохраняем полный промпт нового анализа в историю
        full_prompt = f"SYSTEM: {system_prompt}\n\nUSER: {user_prompt}"
        db_ops.save_analys_history(username, new_analys, len(new_msgs), full_prompt)

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


def get_chat_analys(username: str) -> Optional[dict]:
    """Получить анализ чата (через db_ops)."""
    return db_ops.get_chat_analys(username)


def get_meta_analyses_history(limit: int = 20) -> List[dict]:
    """Получить историю мета-анализов."""
    return db_ops.get_meta_analyses_history(limit=limit)


# Импорт для get_analys_status
from ..history_db import ChatSetting