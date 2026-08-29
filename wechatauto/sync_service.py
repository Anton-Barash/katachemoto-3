# -*- coding: utf-8 -*-
"""Синхронизация сообщений из WeChat в PostgreSQL для закреплённых чатов.

Логика:
- Для каждого закреплённого чата проверяется last_sync_time (максимальный create_time в PG).
- Из WeChatDB загружаются сообщения новее last_sync_time.
- Новые сообщения сохраняются в PostgreSQL с пометкой ai_processed=False.
- После синхронизации last_sync_time обновляется.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .history_db import (
    get_pinned_list,
    get_last_sync_time,
    set_last_sync_time,
    save_message,
    get_message_stats,
)

SYNC_BATCH_SIZE = 1000  # Сколько сообщений загружать за раз из WeChatDB


def sync_all_pinned(db, progress_callback=None) -> Dict[str, dict]:
    """Синхронизировать сообщения для всех закреплённых чатов.

    Args:
        db: Экземпляр WeChatDB.
        progress_callback: Опциональная функция(username, stats) для отслеживания прогресса.

    Returns:
        {username: {"saved": int, "total": int, "new": int, "error": str|None}}
    """
    pinned = get_pinned_list()
    if not pinned:
        logging.info("sync_service: no pinned chats to sync")
        return {}

    results = {}
    for username in pinned:
        try:
            stats = _sync_chat(db, username)
            results[username] = stats
            logging.info(
                "sync_service: %s — saved %d new, %d total in PG",
                username, stats["saved"], stats["total"],
            )
            if progress_callback:
                progress_callback(username, stats)
        except Exception as e:
            logging.warning("sync_service: failed for %s: %s", username, e)
            results[username] = {"saved": 0, "total": 0, "new": 0, "error": str(e)}

    return results


def _sync_chat(db, username: str) -> dict:
    """Синхронизировать один чат."""
    last_sync = get_last_sync_time(username)
    max_create_time = last_sync

    # Загрузить сообщения из WeChat (самые новые сначала)
    try:
        wechat_msgs = list(db.get_messages(username, limit=SYNC_BATCH_SIZE))
    except Exception as e:
        raise RuntimeError(f"WeChatDB.get_messages failed: {e}") from e

    if not wechat_msgs:
        # Нет сообщений в WeChat для этого чата
        pg_stats = get_message_stats(username)
        return {"saved": 0, "total": pg_stats["total"], "new": 0}

    # wechat_msgs приходят от newest к oldest
    # Ищем новые сообщения (create_time > last_sync)
    new_msgs = []
    for m in wechat_msgs:
        ct = m.get("create_time", 0) or 0
        if ct > last_sync:
            new_msgs.append(m)
            if ct > max_create_time:
                max_create_time = ct
        else:
            # Так как сообщения отсортированы от новых к старым,
            # как только встретили create_time <= last_sync — дальше все старые
            break

    saved = 0
    for m in reversed(new_msgs):  # Сохраняем от старых к новым
        try:
            save_message(
                username=username,
                local_id=m.get("local_id", 0) or 0,
                sender=m.get("sender_username", "") or "",
                sender_name="",
                content=m.get("content", "") or "",
                msg_type=m.get("type", "") or "",
                create_time=m.get("create_time", 0) or 0,
                is_self=m.get("sender_id") == 2,
            )
            saved += 1
        except Exception:
            # Если сообщение уже существует (duplicate local_id) — пропускаем
            continue

    # Обновить время последней синхронизации
    if max_create_time > last_sync:
        set_last_sync_time(username, max_create_time)

    # Если были новые сообщения — установить флаг для анализа
    if saved > 0:
        try:
            from .analyser.db_ops import mark_has_new_messages
            mark_has_new_messages(username, True)
        except Exception:
            pass

    pg_stats = get_message_stats(username)
    return {
        "saved": saved,
        "total": pg_stats["total"],
        "new": pg_stats["unprocessed"],
    }


def get_sync_status() -> Dict[str, dict]:
    """Получить статус синхронизации для всех чатов."""
    pinned = get_pinned_list()
    status = {}
    for username in pinned:
        stats = get_message_stats(username)
        last_sync = get_last_sync_time(username)
        status[username] = {
            "total": stats["total"],
            "processed": stats["processed"],
            "unprocessed": stats["unprocessed"],
            "last_sync_time": last_sync,
        }
    return {"pinned_count": len(pinned), "chats": status}