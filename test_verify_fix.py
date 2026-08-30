# -*- coding: utf-8 -*-
"""Проверка исправления через публичное API get_messages."""
import sys
import re
from collections import Counter

sys.path.insert(0, ".")

from wechatauto.db import WeChatDB

TARGET_CHAT = "26279716123@chatroom"

db = WeChatDB()
nick = db.get_nickname

msgs = db.get_messages(TARGET_CHAT, limit=2000)
print("Получено сообщений:", len(msgs))

# Отправители по медиа/тексту из public API
by_sender = Counter()
for m in msgs:
    s = m.get("sender_username", "")
    by_sender[s or f"(id={m.get('sender_id')})"] += 1

print("\n--- sender_username по get_messages (после исправления) ---")
for k, v in by_sender.most_common():
    print(f"  {k!s:<30} nick={nick(k) if k and not str(k).startswith('(') else ''!r:<20} x{v}")

# Проверяем: не должно быть wxid_6767565569112 (立 58 собака)
bad = [m for m in msgs if m.get("sender_username") in ("wxid_6767565569112", "xier3384", "wxid_s4cj6rj428cf22")]
print("\nОстались «посторонние» отправители (должно быть 0):", len(bad))
for b in bad[:5]:
    print("   ", b["local_id"], b["type"], b["sender_username"])

# Проверяем присутствие правильных отправителей
for expect in ("wxid_wyux2b5rozp612", "wxid_ok0qr00cy7yk22", "wxid_qt6186dvb5yk22"):
    n = sum(1 for m in msgs if m.get("sender_username") == expect)
    print(f"  {expect} ({nick(expect)!r}): {n} сообщений")
