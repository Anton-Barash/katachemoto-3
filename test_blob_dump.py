# -*- coding: utf-8 -*-
import logging
logging.basicConfig(level=logging.CRITICAL)
import sys
from wechatauto import WeChatDB
from wechatauto.db import _extract_text_from_blob

db = WeChatDB()
found = db._msg_conn('26322825635@chatroom')
conn, table = found
rows = conn.execute(
    "SELECT local_id, local_type, message_content, sort_seq FROM %s "
    "WHERE (local_type & 255)=1 ORDER BY sort_seq DESC LIMIT 40" % table
).fetchall()
conn.close()

out = sys.stdout.buffer
count = 0
for r in rows:
    c = r['message_content']
    if not isinstance(c, bytes) or c[:4] != b'\x28\xb5\x2f\xfd':
        continue
    count += 1
    if count > 4:
        break
    t = _extract_text_from_blob(c)
    out.write(b'==== local_id=%s LEN=%d extract=%s\n' % (
        r['local_id'].encode() if isinstance(r['local_id'], str) else str(r['local_id']).encode(),
        len(c),
        (t.encode('utf-8') if t else b'None')))
    # hex dump
    for i in range(0, len(c), 16):
        chunk = c[i:i+16]
        hexs = ' '.join('%02x' % b for b in chunk)
        asc = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        out.write(b'  %04x  %-47s  %s\n' % (i, hexs.encode(), asc.encode()))
    out.write(b'\n')
