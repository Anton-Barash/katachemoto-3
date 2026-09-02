#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests
import json

def main():
    base_url = "http://localhost:5000"
    
    # Проверим /api/messages
    username = "26322825635@chatroom"
    url = f"{base_url}/api/messages?username={username}&limit=10"
    
    print(f"Запрос к {url}...")
    response = requests.get(url)
    
    print(f"Статус: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"Тип данных: {type(data)}")
        print(f"Ключи: {list(data.keys())[:10]}")
        if 'messages' in data:
            print(f"Получено сообщений: {len(data['messages'])}")
            for msg in data['messages'][:3]:
                print(f"  {msg['time']} | {msg['sender']} | {msg['content'][:60]}")
        else:
            print(f"Данные: {json.dumps(data[:3], indent=2, ensure_ascii=False)}")
    else:
        print(f"Текст ошибки: {response.text}")

if __name__ == "__main__":
    main()
