#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from wechatauto.history_db import get_session, Message
from datetime import datetime
import sys

def main():
    username = sys.argv[1] if len(sys.argv) > 1 else "26322825635@chatroom"
    s = get_session()
    
    # Подсчитаем сообщения
    cnt = s.query(Message).filter_by(username=username).count()
    print(f"Сообщений в БД для {username}: {cnt}")
    
    # Последнее сообщение
    latest = s.query(Message).filter_by(username=username).order_by(Message.create_time.desc()).first()
    if latest:
        print(f"Последнее сообщение: ID={latest.id}, local_id={latest.local_id}, время={latest.create_time} ({datetime.fromtimestamp(latest.create_time)})")
    
    # Проверим настройки чата
    from wechatauto.history_db import ChatSetting
    chat_settings = s.query(ChatSetting).filter_by(username=username).first()
    if chat_settings:
        print(f"\nНастройки чата:")
        print(f"  alias: {chat_settings.alias}")
        print(f"  is_pinned: {chat_settings.is_pinned}")
        print(f"  last_sync_time: {chat_settings.last_sync_time} ({datetime.fromtimestamp(chat_settings.last_sync_time) if chat_settings.last_sync_time else 'не установлено'})")
    else:
        print(f"\nЧат {username} не найден в chat_settings!")

if __name__ == "__main__":
    main()
