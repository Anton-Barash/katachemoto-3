import streamlit as st
from wechatauto import WeChatDB
from wechatauto.guia import quick_send
import time

st.set_page_config(page_title="wechatauto GUI")
st.title("wechatauto — простой локальный интерфейс")

# Init DB (may take a moment)
@st.cache_data(show_spinner=False)
def get_db():
    return WeChatDB()

db = get_db()

st.sidebar.header("Сессии")
sessions = list(db.get_sessions(limit=500))
if not sessions:
    st.sidebar.write("Нет сессий (проверьте, что WeChat залогинен)")
    st.stop()

choices = [f"{s['username']}  —  {s.get('summary','')[:80]}" for s in sessions]
sel = st.sidebar.selectbox("Выберите чат", choices)
sel_idx = choices.index(sel)
username = sessions[sel_idx]["username"]

st.sidebar.markdown(f"**username:** `{username}`")
st.sidebar.markdown(f"**unread:** {sessions[sel_idx].get('unread',0)}")
st.sidebar.markdown("---")

# Messages panel
st.subheader("Сообщения (последние 50)")
msgs = list(db.get_messages(username, limit=50))
for m in reversed(msgs):  # show oldest → newest
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

st.markdown("Примечания: WeChat должен быть запущен и залогинен; для GUI‑операций рабочий стол должен быть разблокирован и окно WeChat визуально доступно.")