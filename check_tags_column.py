#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from wechatauto.history_db import get_session, ChatSetting

def main():
    s = get_session()
    cols = [c.name for c in ChatSetting.__table__.columns]
    print(f"Columns in chat_settings: {cols}")
    print(f"Has tags column: {'tags' in cols}")

if __name__ == "__main__":
    main()
