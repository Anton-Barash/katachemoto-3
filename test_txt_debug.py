# -*- coding: utf-8 -*-
import logging
logging.basicConfig(level=logging.CRITICAL)
import sys
from wechatauto import WeChatDB
from wechatauto.db import _extract_text_from_blob

db = WeChatDB()
# 直接从原始行读，查看 message_content 的原始 bytes
found = db._msg_conn('26322825635@chatroom')
conn, table = found
rows = conn.execute(
    "SELECT local_type, message_content, sort_seq FROM %s "
    "WHERE (local_type & 255)=1 ORDER BY sort_seq DESC LIMIT 20" % table
).fetchall()
conn.close()

for r in rows:
    c = r['message_content']
    if not isinstance(c, bytes):
        continue
    if c[:4] == b'\x28\xb5\x2f\xfd':
        t = _extract_text_from_blob(c)
        out = sys.stdout.buffer
        out.write(b'LEN=%d head=%r tail=%r extract=%r\n' % (
            len(c), c[:16], c[-8:], (t.encode('utf-8') if t else None)))
