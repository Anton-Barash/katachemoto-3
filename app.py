# -*- coding: utf-8 -*-
"""
Streamlit app for wechatauto with full-screen Settings page (⚙️).
- Main view: select chat, view last messages, send text.
- Settings (full screen): lists Groups and Users separately,
  checkboxes to pin chats, inline rename (no JSON), and search by last message.
Config saved to config.json in the same folder as this file.
"""
import streamlit as st
from pathlib import Path
import json
import time

from wechatauto import WeChatDB
from wechatauto.guia import quick_send

BASE = Path(__file__).resolve().parent
CFG_PATH = BASE / "config.json"


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


def sanitize_key(s):
    return "".join(c if c.isalnum() else "_" for c in s)


def display_name_for(session, cfg):
    uname = session["username"]
    aliases = cfg.get("aliases", {})
    if uname in aliases and aliases[uname]:
        return aliases[uname]
    summary = session.get("summary") or ""
    if summary:
        return f"{summary[:60]} ({uname})"
    return uname


@st.cache_data(show_spinner=False)
def get_db():
    return WeChatDB()


def get_last_message_snippet(db, username):
    try:
        msgs = list(db.get_messages(username, limit=1))
        if not msgs:
            return ""
        m = msgs[0]
        content = m.get("content") or f"[{m.get('type')}]"
        # one-line snippet
        return content.replace("\n", " ")[:400]
    except Exception:
        return ""


# ----------------- App start -----------------
st.set_page_config(page_title="wechatauto GUI", layout="wide")
cfg = load_config()
db = get_db()

st.title("wechatauto — интерфейс")

# detect if we are in settings mode via query param
qp = st.query_params
settings_mode = qp.get("settings", "0") == "1"

# Top bar: show gear on right to open settings
col_left, col_right = st.columns([1, 0.07])
with col_right:
    if st.button("⚙️", key="open_settings_button"):
        st.query_params.from_dict({"settings": "1"})
        st.rerun()

if settings_mode:
    # Full-screen settings page
    col_title, col_exit = st.columns([0.9, 0.1])
    with col_title:
        st.header("Настройки — список чатов (онлайн, полный экран)")
    with col_exit:
        if st.button("✖️ Закрыть", key="close_settings_top"):
            st.query_params.clear()
            st.rerun()
    st.markdown(
        "Здесь можно: "
        "- искать по последнему сообщению, "
        "- отметить чат как `Pinned` (поднять в главный список), "
        "- задать подпись (alias) простым текстом."
    )

    # Load sessions
    sessions = list(db.get_sessions(limit=1000))
    if not sessions:
        st.warning("Не удалось получить сессии. Убедитесь, что WeChat запущен и залогинен.")
        if st.button("Закрыть настройки"):
            st.query_params.clear()
            st.rerun()
        st.stop()

    username_map = {s["username"]: s for s in sessions}
    all_usernames = list(username_map.keys())

    # Apply include/exclude
    if cfg.get("include"):
        visible_usernames = [u for u in cfg["include"] if u in username_map]
    else:
        visible_usernames = [u for u in all_usernames if u not in cfg.get("exclude", [])]

    groups = [u for u in visible_usernames if "@chatroom" in u]
    users = [u for u in visible_usernames if "@chatroom" not in u]

    st.subheader("Поиск по последнему сообщению")
    search = st.text_input("Поиск (по содержимому последнего сообщения, регистронезависимо)").strip()

    # Prepare editable state containers
    edits = st.session_state.get("settings_edits", {})
    if not isinstance(edits, dict):
        edits = {}
    # initialize edits for visible usernames
    for u in visible_usernames:
        if u not in edits:
            edits[u] = {
                "alias": cfg.get("aliases", {}).get(u, display_name_for(username_map[u], cfg)),
                "pinned": u in cfg.get("pinned", []),
            }
    st.session_state["settings_edits"] = edits

    def filter_by_search(usernames):
        if not search:
            return usernames
        out = []
        q = search.lower()
        for u in usernames:
            snippet = get_last_message_snippet(db, u).lower()
            # also search in display name and username
            display = edits[u]["alias"].lower() if edits.get(u) else ""
            if q in snippet or q in u.lower() or q in display:
                out.append(u)
        return out

    st.markdown("---")
    st.subheader("Groups")
    filtered_groups = filter_by_search(groups)
    if not filtered_groups:
        st.write("_Нет групп по фильтру_")
    else:
        for u in filtered_groups:
            s = username_map[u]
            last_msg = get_last_message_snippet(db, u)
            cols = st.columns([0.06, 0.44, 0.34, 0.16])
            # pin checkbox
            pin_key = f"settings_pin_{sanitize_key(u)}"
            edits[u]["pinned"] = cols[0].checkbox("", value=edits[u]["pinned"], key=pin_key)
            # alias text input
            alias_key = f"settings_alias_{sanitize_key(u)}"
            edits[u]["alias"] = cols[1].text_input("", value=edits[u]["alias"], key=alias_key)
            # username + preview of last message
            cols[2].markdown(f"**`{u}`**  \n{last_msg}")
            # actions
            if cols[3].button("Save", key=f"settings_save_{sanitize_key(u)}"):
                # persist this single chat alias/pin immediately
                cfg.setdefault("aliases", {})[u] = edits[u]["alias"]
                pinned = cfg.get("pinned", [])
                if edits[u]["pinned"] and u not in pinned:
                    pinned.append(u)
                if not edits[u]["pinned"] and u in pinned:
                    pinned.remove(u)
                cfg["pinned"] = pinned
                save_config(cfg)
                st.success(f"Сохранено для {u}")
                st.rerun()

    st.markdown("---")
    st.subheader("Users")
    filtered_users = filter_by_search(users)
    if not filtered_users:
        st.write("_Нет пользователей по фильтру_")
    else:
        for u in filtered_users:
            s = username_map[u]
            last_msg = get_last_message_snippet(db, u)
            cols = st.columns([0.06, 0.44, 0.34, 0.16])
            pin_key = f"settings_pin_{sanitize_key(u)}"
            edits[u]["pinned"] = cols[0].checkbox("", value=edits[u]["pinned"], key=pin_key)
            alias_key = f"settings_alias_{sanitize_key(u)}"
            edits[u]["alias"] = cols[1].text_input("", value=edits[u]["alias"], key=alias_key)
            cols[2].markdown(f"**`{u}`**  \n{last_msg}")
            if cols[3].button("Save", key=f"settings_save_{sanitize_key(u)}"):
                cfg.setdefault("aliases", {})[u] = edits[u]["alias"]
                pinned = cfg.get("pinned", [])
                if edits[u]["pinned"] and u not in pinned:
                    pinned.append(u)
                if not edits[u]["pinned"] and u in pinned:
                    pinned.remove(u)
                cfg["pinned"] = pinned
                save_config(cfg)
                st.success(f"Сохранено для {u}")
                st.rerun()

    st.markdown("---")
    # Bulk save all visible edits
    if st.button("Сохранить все изменения"):
        aliases = cfg.get("aliases", {})
        for u, data in edits.items():
            aliases[u] = data["alias"]
        cfg["aliases"] = aliases
        cfg["pinned"] = [u for u, d in edits.items() if d.get("pinned")]
        save_config(cfg)
        st.success("Все изменения сохранены")
        st.rerun()

    st.markdown("---")
    if st.button("Закрыть настройки"):
        # clear query param
        st.query_params.clear()
        st.rerun()

else:
    # Main app view
    sessions = list(db.get_sessions(limit=200))
    if not sessions:
        st.warning("Нет сессий: убедитесь, что WeChat запущен и залогинен.")
        st.stop()

    username_map = {s["username"]: s for s in sessions}
    all_usernames = list(username_map.keys())

    # Apply include/exclude
    if cfg.get("include"):
        visible_usernames = [u for u in cfg["include"] if u in username_map]
    else:
        visible_usernames = [u for u in all_usernames if u not in cfg.get("exclude", [])]

    groups = [u for u in visible_usernames if "@chatroom" in u]
    users = [u for u in visible_usernames if "@chatroom" not in u]

    pinned_cfg = cfg.get("pinned", [])
    pinned = [u for u in pinned_cfg if u in visible_usernames]

    # Build ordered list: pinned, groups(not pinned), users(not pinned)
    ordered = []
    ordered.extend([u for u in pinned if u in visible_usernames])
    ordered.extend([u for u in groups if u not in ordered])
    ordered.extend([u for u in users if u not in ordered])

    # Left column: compact chat list with checkboxes for quick pin/unpin and rename button that opens settings for that chat
    left_col, main_col = st.columns([0.28, 0.72])
    with left_col:
        st.markdown("### Чаты")
        st.write("Pinned сверху. Отметьте для быстрого закрепления (не сохраняет — откройте ⚙️ чтобы сохранить навсегда).")
        # we will use session_state checkboxes for quick pin
        for u in ordered:
            s = username_map[u]
            display = cfg.get("aliases", {}).get(u, display_name_for(s, cfg))
            # render a line with checkbox and a small rename button (which opens settings for that chat)
            cols = st.columns([0.08, 0.72, 0.2])
            is_pinned = u in pinned
            key_quick_pin = f"quick_pin_{sanitize_key(u)}"
            if key_quick_pin not in st.session_state:
                st.session_state[key_quick_pin] = is_pinned
            checked = cols[0].checkbox("", value=st.session_state[key_quick_pin], key=key_quick_pin)
            cols[1].markdown(f"**{display}**  \n`{u}`")
            if cols[2].button("Rename", key=f"open_settings_for_{sanitize_key(u)}"):
                # open settings and pass which chat to focus via query params
                st.query_params.from_dict({"settings": "1", "focus": u})
                st.rerun()

        st.markdown("---")
        if st.button("Применить quick-pins (сохранить)"):
            new_pinned = []
            for u in visible_usernames:
                if st.session_state.get(f"quick_pin_{sanitize_key(u)}"):
                    new_pinned.append(u)
            cfg["pinned"] = new_pinned
            save_config(cfg)
            st.success("Pinned сохранены (в config.json)")
            st.rerun()

    with main_col:
        # selection dropdown
        choices = [f"{display_name_for(username_map[u],cfg)}  —  `{u}`" for u in ordered]
        sel = st.selectbox("Выберите чат для просмотра/взаимодействия", choices)
        sel_idx = choices.index(sel)
        username = ordered[sel_idx]

        st.markdown(f"**Текущий чат:** {display_name_for(username_map[username],cfg)}  `({username})`")
        st.markdown("---")
        st.subheader("Сообщения (последние 100)")
        msgs = list(db.get_messages(username, limit=100))
        if not msgs:
            st.write("Нет сообщений или они не загружены.")
        else:
            for m in reversed(msgs):
                t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m.get("create_time", 0)))
                sender = m.get("sender_username") or m.get("sender_id")
                content = m.get("content") or f"[{m.get('type')}]"
                st.markdown(f"**{t} — {sender}**  \n{content}")

        st.markdown("---")
        st.subheader("Отправка сообщения")
        col1, col2 = st.columns([4, 1])
        with col1:
            out_text = st.text_input("Текст для отправки")
        with col2:
            send = st.button("Отправить")

        if send:
            if not out_text.strip():
                st.warning("Введите текст")
            else:
                try:
                    quick_send(out_text, username, verify=True)
                    st.success("Команда отправлена (проверка по БД включена)")
                except Exception as e:
                    st.error(f"Ошибка отправки: {e}")

# End of file