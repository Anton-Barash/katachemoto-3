# -*- coding: utf-8 -*-
import logging
logging.basicConfig(level=logging.CRITICAL)
import sys
from wechatauto import WeChatDB

db = WeChatDB()
found = db._msg_conn('26322825635@chatroom')
conn, table = found
rows = conn.execute(
    "SELECT local_id, local_type, message_content, compress_content, packed_info_data, sort_seq "
    "FROM %s WHERE (local_type & 255)=1 ORDER BY sort_seq DESC LIMIT 40" % table
).fetchall()
conn.close()

out = sys.stdout.buffer
count = 0
for r in rows:
    c = r['message_content']
    if not isinstance(c, bytes) or c[:4] != b'\x28\xb5\x2f\xfd':
        continue
    count += 1
    if count > 3:
        break
    out.write(b'==== local_id=%s\n' % str(r['local_id']).encode())
    cc = r['compress_content']
    if isinstance(cc, bytes):
        out.write(b'compress_content len=%d head=%r tail=%r\n' % (len(cc), cc[:24], cc[-12:]))
    else:
        out.write(b'compress_content=%r\n' % (cc,))
    pi = r['packed_info_data']
    if isinstance(pi, bytes):
        out.write(b'packed_info len=%d head=%r tail=%r\n' % (len(pi), pi[:24], pi[-12:]))
    else:
        out.write(b'packed_info=%r\n' % (pi,))
    out.write(b'\n')
