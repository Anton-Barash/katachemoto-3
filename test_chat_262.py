# -*- coding: utf-8 -*-
"""Диагностика: почему в чате 26279716123@chatroom видны «лишние» сообщения
от постороннего пользователя 立 58 собака (wxid_6767565569112).

Вывод теста:
  Сообщения группы хранятся в таблице Msg_f494c606eb1a5cadcb04273374e2ece5
  (md5('26279716123@chatroom')). Поле real_sender_id — это ЧИСЛОВОЙ id,
  который НЕ является глобальным ключом таблицы SenderName2Id.
  Авторитетный отправитель для группового сообщения — префикс
  "wxid_xxx:\n" в самом message_content.

  Например, в этой группе у id=63 текст всегда начинается с
  "wxid_wyux2b5rozp612:\n" (devices Tatyana Gorbenko), но глобальный
  SenderName2Id выдаёт для id=63 -> wxid_6767565569112 (立 58 собака).
  Поэтому blob-сообщения (картинки и др., без читаемого префикса) с id=63
  отображаются как от постороннего 立 58 собака.
"""
import sys
import re
import os
from collections import Counter

sys.path.insert(0, ".")

from wechatauto.db import WeChatDB, _md5_hex

TARGET_CHAT = "26279716123@chatroom"
TARGET_WXID = "wxid_6767565569112"
COMPARE_CHAT = "7446194123@chatroom"  # другой чат для сравнения id=63

db = WeChatDB()
print("=" * 70)
print("Аккаунт:", db.wxid)
print("Целевой чат:", TARGET_CHAT, "-> таблица Msg_%s" % _md5_hex(TARGET_CHAT.encode()))
print("=" * 70)

nick = db.get_nickname
sidx = db._sender_id_index()


def analyze(chat):
    found = db._msg_conn(chat)
    if not found:
        print(f"\n=== {chat}: таблица не найдена ===")
        return
    conn, table = found
    rows = conn.execute(
        "SELECT local_id, real_sender_id, message_content FROM %s ORDER BY sort_seq DESC" % table
    ).fetchall()
    conn.close()
    print(f"\n=== {chat} (таблица {table}, строк: {len(rows)}) ===")
    prefix_by_id = Counter()
    blob_by_id = Counter()
    for r in rows:
        rid = r["real_sender_id"]
        c = r["message_content"]
        if isinstance(c, bytes):
            m = re.search(rb"wxid_[A-Za-z0-9_]+", c)
            p = m.group(0).decode() if m else ""
        else:
            m = re.match(r"^(wxid_[A-Za-z0-9_]+):", c or "")
            p = m.group(1) if m else ""
        if p:
            prefix_by_id[(rid, p)] += 1
        else:
            blob_by_id[rid] += 1
    print("  id -> префиксы текстовых сообщений (реальные отправители):")
    for (rid, p), n in sorted(prefix_by_id.items(), key=lambda x: -x[1])[:8]:
        print(f"    id={rid!s:<5} prefix={p:<26} x{n}   (nick={nick(p)!r})")
    print("  id -> blob-сообщения (картинки/др., префикса нет):")
    for rid, n in blob_by_id.most_common(8):
        resolved = sidx.get(int(rid)) if rid else ""
        print(f"    id={rid!s:<5} x{n:<5} глобальное разрешение SenderName2Id -> {resolved!r} (nick={nick(resolved)!r})")


analyze(TARGET_CHAT)
analyze(COMPARE_CHAT)

print("\n" + "=" * 70)
print("ИТОГ:")
resolved = sidx.get(63)
print(f"  real_sender_id=63 в целевом чате = {resolved!r} ({nick(resolved)!r}) по SenderName2Id,")
print("  но ВСЕ текстовые сообщения с id=63 в этом чате имеют префикс wxid_wyux2b5rozp612 (Tatyana).")
print("  => числовой id локальный для чата/устаревший, глобальный SenderName2Id не применим.")
print("  => blob-сообщения (картинки) с id=63 показываются как от постороннего", TARGET_WXID)
print("  => «лишние сообщения в чужом чате».")
print("=" * 70)
