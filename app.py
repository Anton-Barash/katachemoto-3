import streamlit as st
from wechatauto import WeChatDB
from wechatauto.guia import quick_send
import time
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
CFG_PATH = BASE / "config.json"

# ------- config helpers -------
def default_config():
    return {
        "aliases": {
            # "25814625747@chatroom": "Irene Group"
        },
        "include": [],  # если непустой — показываем только эти username'ы
        "exclude": []   # исключаем эти username'ы
    }

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

def display_name_for(session, cfg):
    uname = session["username"]
    aliases = cfg.get("aliases", {})
    if uname in aliases and aliases[uname]:
        return aliases[uname]
    # fallback: use summary or username
    summary = session.get("summary") or ""
    if summary:
        return f"{summary[:60]} ({uname})"
    return uname

# ------- UI -------
st.set_page_config(page_title="wechatauto GUI", layout="wide")
st.title("wechatauto — локальный интерфейс")

cfg = load_config()

@st.cache_data(show_spinner=False)
def get_db():
    return WeChatDB()

db = get_db()

st.sidebar.header("Сессии / Фильтры")
sessions = list(db.get_sessions(limit=1000))

if not sessions:
    st.sidebar.write("Нет сессий (проверьте, что WeChat залогинен)")
    st.stop()

# Build username -> session map
username_map = {s["username"]: s for s in sessions}
all_usernames = list(username_map.keys())

# Apply include/exclude from cfg
if cfg.get("include"):
    visible_usernames = [u for u in cfg["include"] if u in username_map]
else:
    visible_usernames = [u for u in all_usernames if u not in cfg.get("exclude", [])]

# Sidebar: quick selector with display names
choices = [f"{display_name_for(username_map[u], cfg)} | {u}" for u in visible_usernames]
sel_idx = 0
if choices:
    sel = st.sidebar.selectbox("Выберите чат", choices)
    sel_idx = choices.index(sel)
    username = visible_usernames[sel_idx]
else:
    st.sidebar.write("Нет видимых чатов по текущим фильтрам")
    username = None

st.sidebar.markdown("---")
st.sidebar.subheader("Управление aliases / фильтрами")
with st.sidebar.expander("Просмотр и редактирование aliases (username → имя)"):
    aliases_text = json.dumps(cfg.get("aliases", {}), ensure_ascii=False, indent=2)
    new_aliases_text = st.text_area("JSON aliases", aliases_text, height=160)
    if st.button("Сохранить aliases"):
        try:
            new_aliases = json.loads(new_aliases_text)
            cfg["aliases"] = new_aliases
            save_config(cfg)
            st.success("Aliases сохранены")
            st.experimental_rerun()
        except Exception as e:
            st.error(f"Ошибка JSON: {e}")

with st.sidebar.expander("Настройка include / exclude"):
    st.write("Include: если список непустой — будут показаны только эти username'ы.")
    include_sel = st.multiselect("Include (выберите чаты, которые хотите показывать)", all_usernames, default=cfg.get("include", []))
    exclude_sel = st.multiselect("Exclude (исключить из показа)", all_usernames, default=cfg.get("exclude", []))
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Сохранить include"):
            cfg["include"] = include_sel
            # when include set, exclude ignored by design but still stored
            save_config(cfg)
            st.success("include сохранён")
            st.experimental_rerun()
    with col2:
        if st.button("Сохранить exclude"):
            cfg["exclude"] = exclude_sel
            save_config(cfg)
            st.success("exclude сохранён")
            st.experimental_rerun()

st.sidebar.markdown("---")
st.sidebar.markdown(f"config.json: `{CFG_PATH.name}`")
if st.sidebar.button("Открыть config.json в редакторе (локально)"):
    st.sidebar.write("Файл сохраняется в той же папке, где app.py — откройте его в редакторе для ручного правления.")

# ------- Main panel -------
if username:
    st.sidebar.markdown(f"**username:** `{username}`")
    st.sidebar.markdown(f"**display name:** {display_name_for(username_map[username], cfg)}")
    st.sidebar.markdown(f"**unread:** {username_map[username].get('unread',0)}")
    st.markdown("---")

    st.subheader("Сообщения (последние 100)")
    msgs = list(db.get_messages(username, limit=100))
    if not msgs:
        st.write("Нет сообщений или они не загружены.")
    else:
        for m in reversed(msgs):  # oldest -> newest
            t = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(m.get("create_time", 0)))
            sender = m.get("sender_username") or m.get("sender_id")
            content = m.get("content") or f"[{m.get('type')}]"
            st.markdown(f"**{t} — {sender}**  \n{content}")

    st.markdown("---")
    st.subheader("Отправка сообщения")
    col1, col2 = st.columns([4,1])
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

else:
    st.write("Нет выбранного чата (проверьте фильтры).")