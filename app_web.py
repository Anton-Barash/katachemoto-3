#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
wechatauto Web GUI - простой HTML интерфейс
- Настройки: отдельная страница с группами и пользователями
- Сортировка по времени последнего сообщения
- Редактирование имен (aliases)

Исправление: ленивое создание WeChatDB и обработка ошибок, чтобы
Flask не падал на импорте, если WeChat/DB недоступны.
"""
from flask import Flask, render_template, jsonify, request
from pathlib import Path
import json
import time
import hashlib
import os
import re
import logging

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(name)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s')

from wechatauto import WeChatDB
from wechatauto.media import MediaDownloader
from wechatauto.guia import quick_send
from wechatauto.history_db import (
    init_db, get_all_settings, get_pinned_list, get_aliases,
    set_alias as db_set_alias, set_pinned as db_set_pinned,
    upsert_setting,
    get_global_prompt, set_global_prompt,
    get_effective_prompt,
    get_unprocessed_messages, mark_messages_processed,
    get_message_stats,
    get_messages as pg_get_messages,
    get_pg_message_count,
    save_message,
    set_ai_analysis, get_ai_analysis, get_ai_analysis_history,
    unpin_and_cleanup,
)
from wechatauto.sync_service import sync_all_pinned, get_sync_status
from wechatauto.analyser import (
    run_analys, run_meta_analys, get_analys_status,
    get_chat_analys, get_meta_analyses_history,
)
from wechatauto.analyser.db_ops import get_recent_analyses

BASE = Path(__file__).resolve().parent
CFG_PATH = BASE / "config.json"

# ------------------------------------------------------------------
# Форматирование текста сообщений (XML → человекочитаемый вид)
# ------------------------------------------------------------------

def _xml_text(s, tag):
    """Безопасное извлечение текста из тега (без полного парсинга XML, так как
    сообщения часто содержат битые/усечённые XML)."""
    if tag not in s:
        return ""
    open_ = "<" + tag
    close_ = "</" + tag + ">"
    try:
        start = s.index(open_)
        # пропускаем атрибуты тега: ищем '>' после open_
        gt = s.index(">", start + len(open_))
        inner_start = gt + 1
        end = s.index(close_, inner_start)
        v = s[inner_start:end]
    except (ValueError, IndexError):
        return ""
    # вычищаем CDATA
    if v.startswith("<![CDATA[") and v.endswith("]]>"):
        v = v[9:-3]
    return v.strip()


def prettify_message_content(content: str, mtype: str = "") -> str:
    """Заменить XML/контейнеры на понятный текст. Возвращает строку
    с читабельным содержанием или исходный текст, если преобразование
    не требуется / не удалось."""
    if not isinstance(content, str):
        return content
    stripped = content.strip()
    if not stripped:
        return content

    # --- 0. Голосовые/видеозвонки (voipmsg) — служебные, без смысловой
    # нагрузки для анализа переписки. Заменяем на компактную метку. --------
    if "<voipmsg" in stripped.lower():
        status = re.search(r"<msg>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</msg>", stripped, re.S)
        dur = re.search(r'duration\s*=\s*"(\d+)"', stripped) or re.search(
            r"<duration>(\d+)</duration>", stripped
        )
        suffix = ""
        if dur:
            secs = int(dur.group(1))
            suffix = " (%d:%02d)" % (secs // 60, secs % 60) if secs else ""
        if status and status.group(1).strip():
            return "[📞 Звонок%s] %s" % (suffix, status.group(1).strip())
        return "[📞 Звонок%s]" % suffix

    # --- 1. Отмена сообщения (sysmsg revokemsg) ------------------------------
    if stripped.startswith("<?xml") and "revokemsg" in stripped:
        who = _xml_text(stripped, "content") or _xml_text(stripped, "oldmsgid") or ""
        if who:
            # очищаем кавычки WeChat-клиента (уже почти читаемо)
            return "🚫 " + who.strip()
        return "🚫 Сообщение отменено отправителем."

    # --- 2. Системные сообщения sysmsg (добавление в группу / приглашения) ---
    if stripped.startswith("<?xml") and "<sysmsg" in stripped:
        t = _xml_text(stripped, "content") or _xml_text(stripped, "text")
        if t:
            return "ℹ️ " + t
        # приглашения в группу
        for tag in ("memberlist", "newxml", "username"):
            if tag in stripped:
                who = _xml_text(stripped, "content") or ""
                if who:
                    return "ℹ️ " + who
                break

    # --- 3. Emoji (WeChat стикеры) <msg><emoji ...> ------------------------
    if stripped.startswith("<msg>") and "<emoji " in stripped:
        md5 = re.search(r'md5\s*=\s*"([A-Fa-f0-9]{32})"', stripped)
        fromu = re.search(r'fromusername\s*=\s*"([^"]+)"', stripped)
        label = "[Стикер emoji]"
        if md5:
            label = "[Стикер %s]" % md5.group(1)[:8]
        return label

    # --- 4. Изображение <msg><img aeskey=...> ------------------------------
    if "<img " in stripped and ("aeskey" in stripped or "cdnurl" in stripped or "cdnmidimgurl" in stripped):
        label = "[🖼️ Изображение]"
        return label

    # --- 5. Видео <videomsg ...> ------------------------------------------
    if "<videomsg " in stripped or "<video " in stripped:
        title = _xml_text(stripped, "title") or _xml_text(stripped, "des")
        return "[🎥 Видео%s]" % (f" — {title}" if title else "")

    # --- 6. Карточка / ссылка / мини-приложение <appmsg> -------------------
    if "<appmsg" in stripped:
        mtype_val = _xml_text(stripped, "type")
        title = _xml_text(stripped, "title") or _xml_text(stripped, "sourcedisplayname")
        des = _xml_text(stripped, "des")
        url = _xml_text(stripped, "url")
        label_map = {
            "3": "🎵 Музыка",
            "4": "📹 Видео",
            "5": "🔗 Ссылка",
            "6": "📁 Файл",
            "7": "🔗 Ссылка",
            "8": "🔗 Картинка-ссылка",
            "9": "💬 История",
            "15": "🎁 Карточка",
            "17": "📍 Геолокация",
            "19": "💳 Транзакция",
            "20": "💳 Карточка",
            "24": "🎁 Референс",
            "29": "🧩 Мини-приложение",
            "30": "🔗 Запись",
            "31": "🎽 Товар",
            "33": "🎙️ Голосовое",
            "34": "📝 Опрос",
            "36": "📁 Документ",
            "38": "💬 Сообщение-референс",
            "51": "💼 Контакт",
            "57": "📋 Карточка-статья",
            "62": "👋 Пэт-пат",
            "63": "📝 Квитанция",
            "66": "🎁 Красный пакет",
            "2000": "📝 Перевод средств",
            "2001": "💸 Получен перевод",
            "2002": "💳 Перевод",
            "2003": "💳 Счёт",
        }
        if mtype_val in label_map:
            prefix = label_map[mtype_val]
        else:
            prefix = ("📋 Карточка (тип %s)" % mtype_val) if mtype_val else "📋 Карточка"
        parts = []
        if title:
            parts.append(title[:80])
        if des and des != title:
            parts.append(des[:100])
        if parts:
            suffix = (f" — {url[:60]}" if url else "")
            return f"[{prefix}] " + " · ".join(parts) + suffix
        if url:
            return f"[{prefix}] {url[:100]}"
        return f"[{prefix}]"

    # --- 7. Карточка нового контакта / запрос в друзья (WeChat 4.x) -------
    # <msg bigheadimgurl=... username="v3_...@stranger" nickname="Elsa" .../>
    if stripped.startswith("<?xml") and "<msg " in stripped and "nickname=" in stripped:
        nick = re.search(r'\bnickname\s*=\s*"([^"]*)"', stripped)
        province = re.search(r'\bprovince\s*=\s*"([^"]*)"', stripped)
        city = re.search(r'\bcity\s*=\s*"([^"]*)"', stripped)
        who = nick.group(1) if nick and nick.group(1) else ""
        loc = "/".join(
            p for p in (
                (province.group(1) if province else ""),
                (city.group(1) if city else ""),
            ) if p
        )
        label = "👤 Новый контакт"
        if who:
            label += f" — {who}"
        if loc:
            label += f" ({loc})"
        return f"[{label}]"

    # --- 8. Пересылка нескольких сообщений (шаблон записи группы) ----------
    if stripped.startswith("<?xml") and "<msg " in stripped:
        # Универсальное: показываем title если есть
        title = _xml_text(stripped, "title")
        if title:
            return "[ℹ️ Системное] " + title

    return content


app = Flask(__name__)

# don't instantiate WeChatDB at import time — do it lazily and handle errors
_db_instance = None
_db_init_attempted = False

_media_downloader = None


def _get_media_downloader(db):
    """Thread-safe lazy MediaDownloader. Извлекает cfg_dword один раз и кэширует."""
    global _media_downloader
    if _media_downloader is None:
        cfg_dword = getattr(db, "cfg_dword", None)
        if cfg_dword is None:
            try:
                auto = db.extract_master_key()
                if auto:
                    cfg_dword = auto[1]
            except Exception:
                cfg_dword = None
        _media_downloader = MediaDownloader(db, cfg_dword=cfg_dword)
    return _media_downloader


def _migrate_config_to_db():
    """Перенести настройки из config.json в PostgreSQL (однократно)."""
    cfg_path = BASE / "config.json"
    if not cfg_path.exists():
        return
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf8"))
    except Exception:
        return
    if not cfg or not any([cfg.get("aliases"), cfg.get("pinned")]):
        return

    pg_settings = get_all_settings()
    migrated = 0

    for username, alias in cfg.get("aliases", {}).items():
        if username not in pg_settings and alias:
            upsert_setting(username, alias=alias)
            migrated += 1

    for username in cfg.get("pinned", []):
        existing = pg_settings.get(username)
        if existing and existing["is_pinned"]:
            continue
        upsert_setting(username, is_pinned=True)
        migrated += 1

    if migrated:
        logging.info("Migrated %d settings from config.json to PostgreSQL", migrated)
        # Переименовать старый config.json как резервную копию
        backup = cfg_path.with_suffix(".json.bak")
        if not backup.exists():
            cfg_path.rename(backup)


# Инициализация PostgreSQL при старте
try:
    init_db()
    _migrate_config_to_db()
    logging.info("PostgreSQL history_db initialized")
except Exception as e:
    logging.warning("PostgreSQL init failed: %s", e)


def get_db():
    """
    Lazy initializer for WeChatDB. Returns None on failure and logs the error.
    Use this instead of a global db variable.
    """
    global _db_instance, _db_init_attempted
    if _db_instance is not None:
        return _db_instance
    if _db_init_attempted:
        return None
    _db_init_attempted = True
    try:
        _db_instance = WeChatDB()
        return _db_instance
    except Exception as e:
        logging.warning("WeChatDB init failed: %s", e)
        _db_instance = None
        return None


def get_last_message_info(db, username):
    """Get last message content and timestamp. Returns safe defaults if db missing."""
    if not db:
        return {"content": "", "time": 0}
    try:
        msgs = list(db.get_messages(username, limit=1))
        if not msgs:
            return {"content": "", "time": 0}
        m = msgs[0]
        content = m.get("content") or f"[{m.get('type')}]"
        content = content.replace("\n", " ")
        if len(content) > 200:
            content = content[:200] + "..."
        return {
            "content": content,
            "time": m.get("create_time", 0)
        }
    except Exception:
        logging.warning("get_last_message_info failed for %s", username)
        return {"content": "", "time": 0}


@app.route('/')
def index():
    data = get_enriched_sessions()
    return render_template('index.html', data_json=json.dumps(data, ensure_ascii=False))


@app.route('/settings')
def settings_page():
    data = get_enriched_sessions()
    return render_template('settings.html', data_json=json.dumps(data, ensure_ascii=False))


def get_enriched_sessions():
    """Get all sessions with basic info. No per-session DB queries (avoids hangs on corrupted DB)."""
    # Настройки из PostgreSQL
    try:
        settings = get_all_settings()
        pg_aliases = {u: s["alias"] for u, s in settings.items() if s.get("alias")}
        pg_pinned = [u for u, s in settings.items() if s.get("is_pinned")]
    except Exception:
        logging.warning("Failed to load settings from PostgreSQL")
        settings = {}
        pg_aliases = {}
        pg_pinned = []

    db = get_db()
    if not db:
        return {"groups": [], "users": [], "db_error": "WeChat database not available"}
    try:
        sessions = list(db.get_sessions(limit=1000))
    except Exception:
        logging.warning("Failed to read sessions from WeChatDB")
        return {"groups": [], "users": [], "db_error": "Error reading sessions"}

    # Загрузить все никнеймы одним запросом вместо N отдельных
    try:
        nickname_index = db.get_nickname_index()
    except Exception:
        nickname_index = {}

    enriched = []
    for s in sessions:
        try:
            username = s["username"]
            alias = pg_aliases.get(username)
            nickname = nickname_index.get(username, "") or ""
            # Если alias не задан — сохраняем nickname из WeChat в БД
            if alias is None and nickname:
                try:
                    upsert_setting(username, alias=nickname)
                    alias = nickname
                except Exception:
                    pass
            display_name = alias or nickname or username
            setting = settings.get(username)
            if setting is not None:
                is_group = setting["is_group"]
            else:
                is_group = "@chatroom" in username
                try:
                    upsert_setting(username, is_group=is_group)
                except Exception:
                    pass
            enriched.append({
                "username": username,
                "display_name": display_name,
                "nickname": nickname,
                "alias": alias,
                "is_group": is_group,
                "chat_type": "",
                "summary": s.get("summary", "") or "",
                "last_message": s.get("summary", "") or "",
                "last_time": s.get("last_time", 0) or 0,
                "is_pinned": username in pg_pinned,
                "has_new_messages": bool(setting.get("has_new_messages")) if setting else False,
                "has_analys": bool(setting.get("analys")) if setting else False,
                "tags": setting.get("tags", "") if setting else "",
            })
        except Exception:
            continue

    # Определить тип чата
    for x in enriched:
        u = x["username"]
        if "@chatroom" in u:
            x["chat_type"] = "group"
        elif u.startswith("gh_"):
            x["chat_type"] = "official"
        elif u in ("brandsessionholder", "brandservicesessionholder"):
            x["chat_type"] = "system"
        else:
            x["chat_type"] = "user"

    # Сортировка: сначала чаты с новыми сообщениями, потом по времени
    enriched.sort(key=lambda x: (0 if x["has_new_messages"] else 1, x["last_time"] or 0), reverse=True)
    # Но has_new_messages должен быть вверху, поэтому сделаем двойную сортировку
    enriched.sort(key=lambda x: (0 if x["has_new_messages"] else 1, -(x["last_time"] or 0)))
    groups = [x for x in enriched if x["chat_type"] == "group"]
    users = [x for x in enriched if x["chat_type"] == "user"]
    officials = [x for x in enriched if x["chat_type"] == "official"]
    system_chats = [x for x in enriched if x["chat_type"] == "system"]
    return {"groups": groups, "users": users, "officials": officials, "system_chats": system_chats}


@app.route('/api/sessions')
def api_sessions():
    return jsonify(get_enriched_sessions())


@app.route('/api/update_alias', methods=['POST'])
def update_alias():
    data = request.get_json()
    username = data["username"]
    alias = data.get("alias", "").strip()

    try:
        db_set_alias(username, alias)
        return jsonify({"success": True})
    except Exception as e:
        logging.warning("update_alias failed: %s", e)
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/update_pinned', methods=['POST'])
def update_pinned():
    data = request.get_json()
    username = data["username"]
    is_pinned = data.get("is_pinned", False)

    try:
        db_set_pinned(username, is_pinned)
        return jsonify({"success": True})
    except Exception as e:
        logging.warning("update_pinned failed: %s", e)
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/prompt_settings')
def api_prompt_settings():
    """Вернуть глобальный промт и все настройки промтов чатов."""
    try:
        global_prompt = get_global_prompt()
        settings = get_all_settings()
        chat_prompts = {}
        for u, s in settings.items():
            chat_prompts[u] = {
                "use_global": s.get("use_global_prompt", True),
                "custom_prompt": s.get("custom_prompt"),
            }
        return jsonify({
            "global_prompt": global_prompt,
            "chat_prompts": chat_prompts,
        })
    except Exception as e:
        logging.warning("prompt_settings failed: %s", e)
        return jsonify({"error": str(e)})


@app.route('/api/update_global_prompt', methods=['POST'])
def api_update_global_prompt():
    data = request.get_json()
    prompt = data.get("prompt", "").strip()

    if not prompt:
        return jsonify({"success": False, "error": "Пустой промт"})

    try:
        set_global_prompt(prompt)
        return jsonify({"success": True})
    except Exception as e:
        logging.warning("update_global_prompt failed: %s", e)
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/update_chat_prompt', methods=['POST'])
def api_update_chat_prompt():
    data = request.get_json()
    username = data.get("username", "")
    use_global = data.get("use_global", True)
    custom_prompt = data.get("custom_prompt", "").strip() or None

    if not username:
        return jsonify({"success": False, "error": "Нет username"})

    try:
        upsert_setting(username, use_global_prompt=use_global, custom_prompt=custom_prompt)
        return jsonify({"success": True})
    except Exception as e:
        logging.warning("update_chat_prompt failed: %s", e)
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/update_chat_tags', methods=['POST'])
def update_chat_tags():
    data = request.get_json()
    username = data.get("username", "")
    tags = data.get("tags", "").strip()

    if not username:
        return jsonify({"success": False, "error": "Нет username"})

    try:
        upsert_setting(username, tags=tags)
        return jsonify({"success": True})
    except Exception as e:
        logging.warning("update_chat_tags failed: %s", e)
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/sync_messages', methods=['POST'])
def api_sync_messages():
    """Запустить синхронизацию сообщений для всех закреплённых чатов."""
    db = get_db()
    if not db:
        return jsonify({"success": False, "error": "WeChat DB not available"})

    try:
        results = sync_all_pinned(db)
        total_saved = sum(s.get("saved", 0) for s in results.values())
        total_error = sum(1 for s in results.values() if s.get("error"))
        return jsonify({
            "success": True,
            "total_saved": total_saved,
            "total_error": total_error,
            "chats": len(results),
            "results": results,
        })
    except Exception as e:
        logging.warning("sync_messages failed: %s", e)
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/sync_status')
def api_sync_status():
    """Статус синхронизации для всех закреплённых чатов."""
    try:
        status = get_sync_status()
        return jsonify(status)
    except Exception as e:
        logging.warning("sync_status failed: %s", e)
        return jsonify({"error": str(e)})


@app.route('/api/unpin_cleanup', methods=['POST'])
def api_unpin_cleanup():
    """Открепить чат и удалить историю сообщений, оставив AI-анализ."""
    data = request.get_json()
    username = data.get("username", "")

    if not username:
        return jsonify({"success": False, "error": "Нет username"})

    try:
        result = unpin_and_cleanup(username)
        return jsonify({"success": True, "deleted": result["deleted"]})
    except Exception as e:
        logging.warning("unpin_cleanup failed: %s", e)
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/ai_analysis_history')
def api_ai_analysis_history():
    """История AI-анализов для чата."""
    username = request.args.get("username", "")
    if not username:
        return jsonify({"error": "Нет username"})
    try:
        limit = int(request.args.get("limit", 50))
    except Exception:
        limit = 50
    try:
        history = get_ai_analysis_history(username, limit=limit)
        latest = get_ai_analysis(username)
        return jsonify({"history": history, "latest": latest})
    except Exception as e:
        logging.warning("ai_analysis_history failed: %s", e)
        return jsonify({"error": str(e)})


# ─── Analyser (ИИ-анализ чатов) ─────────────────────────────────


@app.route('/api/run_analys', methods=['POST'])
def api_run_analys():
    """Запустить анализ одного чата."""
    data = request.get_json()
    username = data.get("username", "")
    force = data.get("force", False)
    if not username:
        return jsonify({"success": False, "error": "Нет username"})

    try:
        result = run_analys(username, force=force)
        return jsonify(result)
    except Exception as e:
        logging.warning("run_analys failed: %s", e)
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/run_meta_analys', methods=['POST'])
def api_run_meta_analys():
    """Запустить анализ всех анализов."""
    try:
        result = run_meta_analys()
        return jsonify(result)
    except Exception as e:
        logging.warning("run_meta_analys failed: %s", e)
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/analys_status')
def api_analys_status():
    """Статус анализа для чата."""
    username = request.args.get("username", "")
    if not username:
        return jsonify({"error": "Нет username"})
    try:
        status = get_analys_status(username)
        return jsonify(status)
    except Exception as e:
        logging.warning("analys_status failed: %s", e)
        return jsonify({"error": str(e)})


@app.route('/api/chat_analys')
def api_chat_analys():
    """Получить последний анализ чата."""
    username = request.args.get("username", "")
    if not username:
        return jsonify({"error": "Нет username"})
    try:
        data = get_chat_analys(username)
        analys_data = data or {"analys": "", "updated_at": None}
        return jsonify({
            "analys": analys_data["analys"],
            "status": get_analys_status(username),
            "last_updated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()) if analys_data["updated_at"] else ""
        })
    except Exception as e:
        logging.warning("chat_analys failed: %s", e)
        return jsonify({"error": str(e), "analys": ""})


@app.route('/api/recent_analyses')
def api_recent_analyses():
    """Получить последние анализы чата с промптами."""
    username = request.args.get("username", "")
    limit = int(request.args.get("limit", 3))
    if not username:
        return jsonify({"error": "Нет username"})
    try:
        analyses = get_recent_analyses(username, limit=limit)
        return jsonify(analyses)
    except Exception as e:
        logging.warning("recent_analyses failed: %s", e)
        return jsonify({"error": str(e)})


@app.route('/api/meta_analyses_history')
def api_meta_analyses_history():
    """История мета-анализов."""
    try:
        limit = int(request.args.get("limit", 20))
    except Exception:
        limit = 20
    try:
        history = get_meta_analyses_history(limit=limit)
        return jsonify({"history": history})
    except Exception as e:
        logging.warning("meta_analyses_history failed: %s", e)
        return jsonify({"error": str(e)})


@app.route('/api/unprocessed_count')
def api_unprocessed_count():
    """Количество непроанализированных сообщений для чата."""
    username = request.args.get("username", "")
    if not username:
        return jsonify({"error": "Нет username"})
    try:
        from wechatauto.analyser.db_ops import get_unprocessed_messages_since, get_analys_last_msg_id
        last_id = get_analys_last_msg_id(username)
        msgs = get_unprocessed_messages_since(username, last_msg_id=last_id, limit=1000)
        return jsonify({"count": len(msgs)})
    except Exception as e:
        logging.warning("unprocessed_count failed: %s", e)
        return jsonify({"error": str(e)})


@app.route('/api/send', methods=['POST'])
def send_message():
    """Добавить заметку от своего имени в историю чата (без отправки в WeChat).

    Сообщение сохраняется в PostgreSQL с is_self=True и попадает в
    последующий AI-анализ вместе с остальной перепиской.
    """
    data = request.get_json()
    username = data.get("username", "")
    text = (data.get("text") or "").strip()

    if not username:
        return jsonify({"success": False, "error": "Нет username"})
    if not text:
        return jsonify({"success": False, "error": "Пустой текст"})

    try:
        now = int(time.time())
        # Отрицательный local_id гарантирует отсутствие коллизий с
        # реальными local_id из базы WeChat
        local_id = -int(time.time() * 1000)
        save_message(
            username=username,
            local_id=local_id,
            sender="self",
            sender_name="Я",
            content=text,
            msg_type="文本",
            create_time=now,
            is_self=True,
        )
        # Пометить чат: есть новые непроанализированные сообщения
        from wechatauto.analyser.db_ops import mark_has_new_messages
        mark_has_new_messages(username, True)
        return jsonify({"success": True, "create_time": now})
    except Exception as e:
        logging.warning("save self-note failed: %s", e, exc_info=True)
        return jsonify({"success": False, "error": str(e)})


def _format_pg_messages(username: str, limit: int, offset: int = 0, total: int = 0) -> dict:
    """Загрузить и отформатировать сообщения из PostgreSQL (страница limit|offset)."""
    pg_msgs = pg_get_messages(username, limit=limit, offset=offset)
    pg_msgs.reverse()  # от старых к новым
    logging.debug("API messages for %s: %d from PG (offset=%d)", username, len(pg_msgs), offset)

    # Для отображения имён нужен nick_index из WeChatDB
    db = get_db()
    try:
        nick_index = db.get_nickname_index() if db else {}
    except Exception:
        nick_index = {}

    def _display(u):
        return nick_index.get(u, u) if u else u

    is_group = "@chatroom" in username

    # Для личного чата имя собеседника — алиас чата из chat_settings
    # (как в промпте анализа), а не ник из contact.db, который может
    # указывать на другого контакта.
    chat_alias = ""
    if not is_group:
        try:
            chat_alias = get_aliases().get(username, "")
        except Exception:
            chat_alias = ""

    formatted = []
    for m in pg_msgs:
        content = m.get("content") or f"[{m.get('msg_type')}]"
        sender = m.get("sender_username") or ""
        mtype = m.get("msg_type") or ""

        if is_group:
            mm = re.match(r"^(wxid_[A-Za-z0-9_]+|[^:\n]{1,40}):\s*\n?", content)
            if mm:
                content = content[mm.end():]
                if not sender:
                    sender = mm.group(1)

        content = prettify_message_content(content, mtype)
        is_sys = bool(content and content[0] in ("🚫", "ℹ️") and isinstance(content, str))

        quote = (m.get("quote_content") or "").strip()
        quote_sender = ""
        if quote:
            q_sender_id = m.get("quote_sender") or ""
            if not is_group:
                quote_sender = m.get("quote_display") or chat_alias or _display(q_sender_id)
            else:
                quote_sender = m.get("quote_display") or _display(q_sender_id)

        # Для личного чата: имя собеседника — алиас чата, сырой ID не показываем
        if is_group:
            sender_display = _display(sender)
            sender_id = sender
        else:
            sender_display = chat_alias or _display(username)
            sender_id = ""

        formatted.append({
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(m.get("create_time", 0))),
            "sender": sender_display,
            "id": sender_id,
            "content": content,
            "type": mtype,
            "local_id": m.get("local_id"),
            "is_image": mtype == "图片",
            "is_self": m.get("is_self", False),
            "is_sys": is_sys,
            "media_path": m.get("media_path") or "",
            "quote": quote,
            "quote_sender": quote_sender,
            "quote_local_id": m.get("quote_local_id"),
        })
    return {"messages": formatted, "source": "pg", "total": total, "offset": offset}


@app.route('/api/messages')
def api_messages():
    username = request.args.get("username", "")
    try:
        limit = int(request.args.get("limit", 10))
    except Exception:
        limit = 10
    try:
        offset = int(request.args.get("offset", 0))
    except Exception:
        offset = 0

    # 1. Пробуем PostgreSQL (быстро) — всегда, если есть хотя бы одно сообщение
    try:
        pg_count = get_pg_message_count(username)
        if pg_count > 0:
            # Загружаем из PG, даже если меньше лимита
            actual_limit = min(limit, pg_count - offset)
            return jsonify(_format_pg_messages(username, actual_limit, offset, pg_count))
    except Exception as e:
        logging.warning("PG messages failed for %s, fallback to WeChatDB: %s", username, e)

    # 2. PG не хватает — грузим из WeChatDB
    db = get_db()
    if not db:
        return jsonify({"messages": [], "error": "WeChat DB not available"})
    try:
        msgs = list(db.get_messages(username, limit=limit))
    except Exception:
        logging.warning("Failed to read messages for %s", username)
        return jsonify({"messages": []})
    msgs.reverse()
    logging.debug("API messages for %s: %d from WeChatDB", username, len(msgs))

    try:
        nick_index = db.get_nickname_index()
    except Exception:
        nick_index = {}

    def _display(u):
        return nick_index.get(u, u) if u else u

    formatted = []
    is_group = "@chatroom" in username
    for m in msgs:
        content = m.get("content") or f"[{m.get('type')}]"
        sender = m.get("sender_username") or ""
        mtype = m.get("type") or ""

        if is_group:
            mm = re.match(r"^(wxid_[A-Za-z0-9_]+|[^:\n]{1,40}):\s*\n?", content)
            if mm:
                content = content[mm.end():]
                if not sender:
                    sender = mm.group(1)

        content = prettify_message_content(content, mtype)
        is_sys = bool(content and content[0] in ("🚫", "ℹ️") and isinstance(content, str))

        formatted.append({
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(m.get("create_time", 0))),
            "sender": _display(sender),
            "id": sender,
            "content": content,
            "type": mtype,
            "local_id": m.get("local_id"),
            "is_image": mtype == "图片",
            "is_self": m.get("sender_id") == 2,
            "is_sys": is_sys,
            "quote": m.get("quote_content"),
            "quote_sender": m.get("quote_display") or _display(m.get("quote_sender")),
            "quote_local_id": m.get("quote_local_id"),
        })
    return jsonify({"messages": formatted, "source": "wechat", "total": None, "offset": 0})


@app.route('/api/image/<username>/<int:local_id>')
def api_image(username, local_id):
    """Serve a decrypt image for a message in a chat."""
    db = get_db()
    if not db:
        return "", 404
    try:
        dl = _get_media_downloader(db)
        path = dl.download_image(username, local_id, save_dir=str(BASE / "media_cache"))
        if not path or not os.path.isfile(path):
            return "", 404
        ext = os.path.splitext(path)[1].lower()
        mimetype = {
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".wxgf": "image/heic",
        }.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            data = f.read()
        return app.response_class(data, mimetype=mimetype)
    except Exception as e:
        logging.warning("image %s/%s failed: %s", username, local_id, e)
        return "", 404


@app.route('/api/avatar/<username>')
def avatar(username):
    """Serve avatar image for a given username from WeChat files."""
    db = get_db()
    if not db:
        return "", 404
    try:
        # MD5 hash of the username (lowercase) — WeChat avatar filename format
        md5 = hashlib.md5(username.lower().encode()).hexdigest()
        # Search in common avatar directories
        account_dir = db.account_dir
        candidates = [
            os.path.join(account_dir, "avatar", md5 + ".png"),
            os.path.join(account_dir, "avatar", md5 + ".jpg"),
            os.path.join(account_dir, "avatar", md5 + ".jpeg"),
            os.path.join(account_dir, "sns", "avatar", md5 + ".png"),
            os.path.join(account_dir, "sns", "avatar", md5 + ".jpg"),
            os.path.join(account_dir, "sns", "avatar", md5 + ".jpeg"),
        ]
        for path in candidates:
            if os.path.isfile(path):
                ext = os.path.splitext(path)[1].lower()
                mimetype = "image/png" if ext == ".png" else "image/jpeg"
                return app.response_class(open(path, "rb"), mimetype=mimetype)
    except Exception:
        logging.warning("Avatar lookup failed for %s", username)
    return "", 404


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)