#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from wechatauto import WeChatDB

def main():
    db = WeChatDB()
    username = "26322825635@chatroom"
    
    print(f"Получаем сообщения для {username}...")
    msgs = list(db.get_messages(username, limit=10))
    
    print(f"Получено {len(msgs)} сообщений из WeChatDB:")
    for i, m in enumerate(msgs[:5]):
        print(f"\n[{i+1}] local_id={m.get('local_id')}, create_time={m.get('create_time')}")
        print(f"  sender: {m.get('sender_nickname')} ({m.get('sender_id')})")
        print(f"  content: {m.get('content', 'NO CONTENT')[:100]}")

if __name__ == "__main__":
    main()
