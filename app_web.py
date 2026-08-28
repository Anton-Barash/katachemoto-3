#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
wechatauto Web GUI - простой HTML интерфейс
- Настройки: отдельная страница с группами и пользователями
- Сортировка по времени последнего сообщения
- Редактирование имен (aliases)
"""

from flask import Flask, render_template_string, jsonify, request
from pathlib import Path
import json
import time

from wechatauto import WeChatDB
from wechatauto.guia import quick_send

BASE = Path(__file__).resolve().parent
CFG_PATH = BASE / "config.json"

app = Flask(__name__)
db = WeChatDB()


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


def get_last_message_info(db, username):
    """Get last message content and timestamp"""
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
        return {"content": "", "time": 0}


@app.route('/')
def index():
    data = get_enriched_sessions()
    return render_template_string(HTML_INDEX, data_json=json.dumps(data, ensure_ascii=False))


@app.route('/settings')
def settings_page():
    data = get_enriched_sessions()
    return render_template_string(HTML_SETTINGS, data_json=json.dumps(data, ensure_ascii=False))


def get_enriched_sessions():
    """Get all sessions sorted by last message time, returns dict with groups and users"""
    cfg = load_config()
    sessions = list(db.get_sessions(limit=1000))

    enriched = []
    for s in sessions:
        username = s["username"]
        alias = cfg.get("aliases", {}).get(username, None)
        last_info = get_last_message_info(db, username)
        is_group = "@chatroom" in username
        summary = s.get("summary", "") or ""
        nickname = db.get_nickname(username)
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
    alias = data["alias"].strip()

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
    is_pinned = data["is_pinned"]

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
    username = data["username"]
    text = data["text"].strip()

    if not text:
        return jsonify({"success": False, "error": "Пустой текст"})

    try:
        result = quick_send(text, username, verify=True)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/messages')
def api_messages():
    username = request.args.get("username", "")
    limit = int(request.args.get("limit", 100))
    msgs = list(db.get_messages(username, limit=limit))
    # Reverse to show newest at bottom
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


# HTML templates

HTML_INDEX = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>wechatauto</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; }
header { background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 1rem; display: flex; justify-content: space-between; align-items: center; }
h1 { font-size: 1.5rem; color: #333; }
.container { display: flex; height: calc(100vh - 70px); }
.sidebar { width: 320px; background: white; overflow-y: auto; border-right: 1px solid #eee; }
.main { flex: 1; display: flex; flex-direction: column; background: white; }
.chat-item { padding: 0.75rem 1rem; border-bottom: 1px solid #f0f0f0; cursor: pointer; }
.chat-item:hover { background: #fafafa; }
.chat-item.active { background: #e3f2fd; }
.chat-item.pinned { border-left: 3px solid #2196f3; }
.chat-name { font-weight: 500; color: #111; margin-bottom: 0.25rem; }
.chat-id { font-size: 0.8rem; color: #999; }
.chat-last { font-size: 0.85rem; color: #666; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 280px; }
#messages { flex: 1; overflow-y: auto; padding: 1rem; }
.message { margin-bottom: 0.75rem; max-width: 70%; }
.message.self { margin-left: auto; text-align: right; }
.message .sender { font-size: 0.75rem; color: #888; margin-bottom: 0.25rem; }
.message .content { background: #e3f2fd; padding: 0.75rem 1rem; border-radius: 12px; display: inline-block; text-align: left; }
.message.self .content { background: #4caf50; color: white; }
.message .time { font-size: 0.7rem; color: #999; margin-top: 0.25rem; }
.input-area { border-top: 1px solid #eee; padding: 1rem; display: flex; gap: 0.5rem; }
.input-area input { flex: 1; padding: 0.75rem; border: 1px solid #ddd; border-radius: 6px; font-size: 1rem; }
button { padding: 0.75rem 1.5rem; background: #2196f3; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 1rem; }
button:hover { background: #1976d2; }
.settings-btn { background: #fff; border: 1px solid #ddd; color: #333; }
.settings-btn:hover { background: #f5f5f5; }
.no-chat { flex: 1; display: flex; align-items: center; justify-content: center; color: #999; }
h3 { padding: 0.75rem 1rem; border-bottom: 1px solid #eee; font-size: 0.9rem; color: #666; background: #fafafa; }
</style>
</head>
<body>
<header>
    <h1>wechatauto</h1>
    <button class="settings-btn" onclick="location.href='/settings'">⚙️ Настройки</button>
</header>
<div class="container">
    <div class="sidebar">
        <h3>Закрепленные</h3>
        <div id="pinned-list"></div>
        <h3>Все чаты</h3>
        <div id="all-list"></div>
    </div>
    <div class="main" id="main-area">
        <div class="no-chat" id="no-chat-selected">
            Выберите чат из списка слева
        </div>
        <div id="messages" style="display: none;"></div>
        <div class="input-area" id="input-area" style="display: none;">
            <input type="text" id="message-input" placeholder="Введите сообщение...">
            <button onclick="sendMessage()">Отправить</button>
        </div>
    </div>
</div>

<script>
let currentChat = null;
let data = {{ data_json|safe }};
let sessionsData = [...data.groups, ...data.users];

function renderSessions() {
    const pinned = sessionsData.filter(s => s.is_pinned);
    const others = sessionsData.filter(s => !s.is_pinned);

    document.getElementById('pinned-list').innerHTML = pinned.map(s => `
        <div class="chat-item ${currentChat?.username === s.username ? 'active' : ''} ${s.is_pinned ? 'pinned' : ''}"
             onclick="selectChat('${s.username}')">
            <div class="chat-name">${escapeHtml(s.display_name)}</div>
            <div class="chat-id">${escapeHtml(s.username)}</div>
            ${s.last_message ? `<div class="chat-last">${escapeHtml(s.last_message)}</div>` : ''}
        </div>
    `).join('');

    document.getElementById('all-list').innerHTML = others.map(s => `
        <div class="chat-item ${currentChat?.username === s.username ? 'active' : ''}"
             onclick="selectChat('${s.username}')">
            <div class="chat-name">${escapeHtml(s.display_name)}</div>
            <div class="chat-id">${escapeHtml(s.username)}</div>
            ${s.last_message ? `<div class="chat-last">${escapeHtml(s.last_message)}</div>` : ''}
        </div>
    `).join('');
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

async function selectChat(username) {
    currentChat = sessionsData.find(s => s.username === username);
    renderSessions();
    await loadMessages(username);
    document.getElementById('no-chat-selected').style.display = 'none';
    document.getElementById('messages').style.display = 'block';
    document.getElementById('input-area').style.display = 'flex';
}

async function loadMessages(username) {
    const res = await fetch(`/api/messages?username=${encodeURIComponent(username)}&limit=100`);
    const data = await res.json();
    const container = document.getElementById('messages');
    container.innerHTML = data.messages.map(m => `
        <div class="message ${m.is_self ? 'self' : ''}">
            ${!m.is_self ? `<div class="sender">${escapeHtml(m.sender)}</div>` : ''}
            <div class="content">${escapeHtml(m.content).replace(/\\n/g, '<br>')}</div>
            <div class="time">${m.time}</div>
        </div>
    `).join('');
    container.scrollTop = container.scrollHeight;
}

async function sendMessage() {
    if (!currentChat) return;
    const input = document.getElementById('message-input');
    const text = input.value.trim();
    if (!text) return;

    const res = await fetch('/api/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: currentChat.username, text: text })
    });
    const result = await res.json();

    if (result.success) {
        input.value = '';
        setTimeout(() => loadMessages(currentChat.username), 500);
    } else {
        alert('Ошибка отправки: ' + result.error);
    }
}

document.getElementById('message-input').addEventListener('keypress', (e) => {
    if (e.key === 'Enter') sendMessage();
});

renderSessions();
</script>
</body>
</html>
"""

HTML_SETTINGS = """
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Настройки — wechatauto</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; }
header { background: white; box-shadow: 0 1px 3px rgba(0,0,0,0.1); padding: 1rem; display: flex; justify-content: space-between; align-items: center; }
h1 { font-size: 1.5rem; color: #333; }
.container { max-width: 1200px; margin: 2rem auto; padding: 0 1rem; }
.tabs { display: flex; margin-bottom: 1rem; gap: 0.5rem; }
.tab-btn { padding: 0.75rem 1.5rem; background: white; border: 1px solid #ddd; border-radius: 6px 6px 0 0; cursor: pointer; border-bottom: none; margin-bottom: -1px; }
.tab-btn.active { background: #2196f3; color: white; border-color: #2196f3; }
.tab-content { display: none; background: white; border-radius: 0 6px 6px 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); overflow: hidden; }
.tab-content.active { display: block; }
table { width: 100%; border-collapse: collapse; }
th { background: #fafafa; text-align: left; padding: 0.75rem 1rem; border-bottom: 1px solid #eee; font-weight: 600; color: #666; }
td { padding: 0.75rem 1rem; border-bottom: 1px solid #f0f0f0; vertical-align: top; }
tr:hover { background: #fafafa; }
input[type="text"] { width: 100%; padding: 0.5rem 0.75rem; border: 1px solid #ddd; border-radius: 4px; font-size: 1rem; }
.username { font-family: monospace; font-size: 0.9rem; color: #666; }
.last-msg { color: #888; font-size: 0.9rem; max-width: 400px; word-break: break-word; }
.checkbox { width: 18px; height: 18px; cursor: pointer; }
.save-btn { background: #4caf50; color: white; padding: 0.5rem 1rem; border: none; border-radius: 4px; cursor: pointer; }
.save-btn:hover { background: #43a047; }
.saving { opacity: 0.5; pointer-events: none; }
.back-link { text-decoration: none; color: #333; padding: 0.5rem 1rem; border: 1px solid #ddd; border-radius: 6px; background: white; }
.back-link:hover { background: #f5f5f5; }
.search-box { padding: 1rem; border-bottom: 1px solid #eee; }
.search-box input { width: 100%; padding: 0.75rem; border: 1px solid #ddd; border-radius: 6px; font-size: 1rem; }
.pinned-col { width: 60px; text-align: center; }
.name-col { width: 25%; }
.id-col { width: 20%; }
.lastmsg-col { width: 35%; }
.save-col { width: 12%; }
.limit-row { padding: 0.75rem 1rem; border-bottom: 1px solid #eee; font-size: 0.9rem; color: #666; display: flex; align-items: center; gap: 0.5rem; }
.limit-row select { padding: 0.35rem 0.5rem; border: 1px solid #ddd; border-radius: 4px; font-size: 0.9rem; }
.section-title { padding: 0.75rem 1rem; font-size: 0.85rem; font-weight: 600; color: #666; background: #fafafa; border-bottom: 1px solid #eee; margin-top: 0; }
.section-pinned { }
.section-pinned .section-title { color: #2196f3; }
.section-rest { }
</style>
</head>
<body>
<header>
    <h1>⚙️ Настройки чатов</h1>
    <a href="/" class="back-link">← Назад к чатам</a>
</header>
<div class="container">
    <div class="tabs">
        <button class="tab-btn active" data-tab="groups" onclick="switchTab('groups')">Группы</button>
        <button class="tab-btn" data-tab="users" onclick="switchTab('users')">Пользователи</button>
    </div>

    <div id="groups" class="tab-content active">
        <div class="search-box">
            <input type="text" id="search-groups" placeholder="🔍 Поиск по названию, ID или последнему сообщению..." oninput="filterTable('groups')">
        </div>
        <div class="limit-row">
            <span>Показывать:</span>
            <select id="limit-groups" onchange="renderTable('groups')">
                <option value="10">10</option>
                <option value="25" selected>25</option>
                <option value="45">45</option>
            </select>
        </div>
        <div id="groups-pinned-section" class="section-pinned"></div>
        <div id="groups-rest-section" class="section-rest"></div>
    </div>

    <div id="users" class="tab-content">
        <div class="search-box">
            <input type="text" id="search-users" placeholder="🔍 Поиск по названию, ID или последнему сообщению..." oninput="filterTable('users')">
        </div>
        <div class="limit-row">
            <span>Показывать:</span>
            <select id="limit-users" onchange="renderTable('users')">
                <option value="10">10</option>
                <option value="25" selected>25</option>
                <option value="45">45</option>
            </select>
        </div>
        <div id="users-pinned-section" class="section-pinned"></div>
        <div id="users-rest-section" class="section-rest"></div>
    </div>
</div>

<script>
let data = {{ data_json|safe }};
let originalData = { groups: [...data.groups], users: [...data.users] };

function switchTab(tab) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.querySelector(`[data-tab="${tab}"]`).classList.add('active');
    document.getElementById(tab).classList.add('active');
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function renderTable(type) {
    if (!data) { setTimeout(() => renderTable(type), 300); return; }
    const items = data[type] || [];
    const limit = parseInt(document.getElementById(`limit-${type}`).value);
    const pinned = items.filter(i => i.is_pinned);
    const rest = items.filter(i => !i.is_pinned).slice(0, limit);

    const pinnedSection = document.getElementById(`${type}-pinned-section`);
    const restSection = document.getElementById(`${type}-rest-section`);

    function makeTable(items) {
        if (items.length === 0) return '<div class="section-title" style="border-bottom: none; color: #999; font-style: italic;">(нет)</div>';
        return `<table>` +
            `<thead><tr><th class="pinned-col">📌</th><th class="name-col">Имя</th><th class="id-col">ID чата</th><th class="lastmsg-col">Последнее сообщение</th><th class="save-col"></th></tr></thead>` +
            `<tbody>${items.map(item => `
                <tr data-username="${item.username}" data-search="${item.display_name} ${item.username} ${item.last_message}">
                    <td class="pinned-col" style="text-align: center;">
                        <input type="checkbox" class="checkbox" ${item.is_pinned ? 'checked' : ''} onchange="updatePinned('${item.username}', this)">
                    </td>
                    <td>
                        <input type="text" value="${escapeHtml(item.display_name)}" id="alias-${item.username}">
                        ${item.nickname && item.nickname !== item.username && item.nickname !== item.display_name ? `<div style="font-size:0.75rem;color:#999;margin-top:2px;">WeChat: ${escapeHtml(item.nickname)}</div>` : ''}
                    </td>
                    <td><div class="username">${escapeHtml(item.username)}</div></td>
                    <td><div class="last-msg">${escapeHtml(item.last_message) || '(нет сообщений)'}</div></td>
                    <td>
                        <button class="save-btn" onclick="saveRow('${type}', '${item.username}')">Сохранить</button>
                    </td>
                </tr>
            `).join('')}</tbody></table>`;
    }

    pinnedSection.innerHTML = `<div class="section-title">📌 Закрепленные (${pinned.length})</div>` + makeTable(pinned);
    restSection.innerHTML = `<div class="section-title">${type === 'groups' ? 'Группы' : 'Пользователи'} (${rest.length} из ${items.length})</div>` + makeTable(rest);
}

function updatePinned(username, checkbox) {
    const item = findItem(username);
    if (item) {
        item.is_pinned = checkbox.checked;
    }
}

function findItem(username) {
    for (const type of ['groups', 'users']) {
        const found = data[type].find(i => i.username === username);
        if (found) return found;
    }
    return null;
}

async function saveRow(type, username) {
    const aliasInput = document.getElementById(`alias-${username}`);
    const newAlias = aliasInput.value.trim();

    const item = findItem(username);
    const newPinned = item.is_pinned;

    const btn = aliasInput.closest('tr').querySelector('.save-btn');
    const oldText = btn.textContent;
    btn.textContent = 'Сохранение...';
    btn.classList.add('saving');

    // Update alias
    await fetch('/api/update_alias', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, alias: newAlias })
    });

    // Update pinned
    await fetch('/api/update_pinned', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, is_pinned: newPinned })
    });

    item.display_name = newAlias || item.username;

    btn.textContent = '✓ Сохранено';
    setTimeout(() => {
        btn.textContent = oldText;
        btn.classList.remove('saving');
    }, 1000);

    // Re-render to move item between pinned/rest sections
    renderTable(type);
}

function filterTable(type) {
    const query = document.getElementById(`search-${type}`).value.toLowerCase();
    const rows = document.querySelectorAll(`#${type} tr`);

    rows.forEach(row => {
        if (!row.dataset.search) return;
        const searchText = row.dataset.search.toLowerCase();
        if (!query || searchText.includes(query)) {
            row.style.display = '';
        } else {
            row.style.display = 'none';
        }
    });
}

renderTable('groups');
renderTable('users');
</script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=False)
