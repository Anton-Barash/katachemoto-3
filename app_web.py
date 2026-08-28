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
import logging

from wechatauto import WeChatDB
from wechatauto.guia import quick_send

BASE = Path(__file__).resolve().parent
CFG_PATH = BASE / "config.json"

app = Flask(__name__)

# don't instantiate WeChatDB at import time — do it lazily and handle errors
_db_instance = None
_db_init_attempted = False


def default_config():
    return {"aliases": {}, "include": [], "exclude": [], "pinned": []}


def load_config():
    if not CFG_PATH.exists():
        save_config(default_config())
    try:
        return json.loads(CFG_PATH.read_text(encoding="utf8"))
    except Exception:
        save_config(default_config())
        return default_config()


def save_config(cfg):
    CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf8")


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
        logging.exception("Failed to initialize WeChatDB: %s", e)
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
        logging.exception("get_last_message_info failed for %s", username)
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
    """Get all sessions sorted by last message time, returns dict with groups and users"""
    cfg = load_config()
    db = get_db()
    if not db:
        return {"groups": [], "users": [], "db_error": "WeChat database not available"}
    try:
        sessions = list(db.get_sessions(limit=1000))
    except Exception:
        logging.exception("Failed to read sessions from WeChatDB")
        return {"groups": [], "users": [], "db_error": "Error reading sessions"}

    enriched = []
    for s in sessions:
        username = s["username"]
        alias = cfg.get("aliases", {}).get(username, None)
        last_info = get_last_message_info(db, username)
        is_group = "@chatroom" in username
        summary = s.get("summary", "") or ""
        try:
            nickname = db.get_nickname(username)
        except Exception:
            nickname = ""
        display_name = alias or nickname or username

        enriched.append({
            "username": username,
            "display_name": display_name,
            "nickname": nickname,
            "alias": alias,
            "is_group": is_group,
            "summary": summary,
            "last_message": last_info["content"],
            "last_time": last_info["time"] or s.get("last_time", 0),
            "is_pinned": username in cfg.get("pinned", [])
        })

    enriched.sort(key=lambda x: x["last_time"], reverse=True)

    groups = [x for x in enriched if x["is_group"]]
    users = [x for x in enriched if not x["is_group"]]

    return {"groups": groups, "users": users}


@app.route('/api/sessions')
def api_sessions():
    return jsonify(get_enriched_sessions())


@app.route('/api/update_alias', methods=['POST'])
def update_alias():
    data = request.get_json()
    username = data["username"]
    alias = data.get("alias", "").strip()

    cfg = load_config()
    if alias:
        cfg.setdefault("aliases", {})[username] = alias
    else:
        cfg.setdefault("aliases", {}).pop(username, None)
    save_config(cfg)

    return jsonify({"success": True})


@app.route('/api/update_pinned', methods=['POST'])
def update_pinned():
    data = request.get_json()
    username = data["username"]
    is_pinned = data.get("is_pinned", False)

    cfg = load_config()
    pinned = cfg.get("pinned", [])

    if is_pinned and username not in pinned:
        pinned.append(username)
    if not is_pinned and username in pinned:
        pinned.remove(username)

    cfg["pinned"] = pinned
    save_config(cfg)

    return jsonify({"success": True})


@app.route('/api/send', methods=['POST'])
def send_message():
    data = request.get_json()
    username = data.get("username", "")
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"success": False, "error": "Пустой текст"})

    db = get_db()
    if not db:
        return jsonify({"success": False, "error": "WeChat DB not available on server"})

    try:
        result = quick_send(text, username, verify=True)
        return jsonify({"success": True})
    except Exception as e:
        logging.exception("quick_send failed")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/messages')
def api_messages():
    db = get_db()
    if not db:
        return jsonify({"messages": [], "error": "WeChat DB not available"})
    username = request.args.get("username", "")
    try:
        limit = int(request.args.get("limit", 100))
    except Exception:
        limit = 100
    try:
        msgs = list(db.get_messages(username, limit=limit))
    except Exception:
        logging.exception("Failed to read messages for %s", username)
        return jsonify({"messages": []})
    msgs.reverse()
    formatted = []
    for m in msgs:
        formatted.append({
            "time": time.strftime("%Y-%m-%d %H:%M", time.localtime(m.get("create_time", 0))),
            "sender": m.get("sender_username") or m.get("sender_id", ""),
            "content": m.get("content") or f"[{m.get('type')}]",
            "is_self": m.get("sender_id") == 2
        })
    return jsonify({"messages": formatted})


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)