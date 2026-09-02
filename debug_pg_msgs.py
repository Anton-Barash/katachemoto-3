#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from wechatauto.history_db import get_session, Message

def main():
    username = "26322825635@chatroom"
    s = get_session()
    
    print(f"Все сообщения из PG для {username}:")
    msgs = s.query(Message).filter_by(username=username).order_by(Message.create_time.asc()).all()
    
    for m in msgs:
        print(f"\nID={m.id}, local_id={m.local_id}, create_time={m.create_time}")
        print(f"  sender_username={m.sender_username}, sender_name={m.sender_name}")
        print(f"  content={m.content[:100]}")
        print(f"  ai_processed={m.ai_processed}, media_path={m.media_path}")

if __name__ == "__main__":
    main()
