# -*- coding: utf-8 -*-
import logging
logging.basicConfig(level=logging.CRITICAL)
import sys
from wechatauto import WeChatDB

db = WeChatDB()
found = db._msg_conn('26322825635@chatroom')
conn, table = found
rows = conn.execute(
    "SELECT local_id, message_content FROM %s "
    "WHERE (local_type & 255)=1 ORDER BY sort_seq DESC LIMIT 60" % table
).fetchall()
conn.close()

import zstandard
out = sys.stdout.buffer
dctx = zstandard.ZstdDecompressor()

def try_offsets(content):
    results = []
    for off in range(0, 16):
        try:
            dec = dctx.decompress(content[off:], max_output_size=200000)
            if dec:
                results.append((off, dec))
        except Exception:
            pass
    return results

count = 0
for r in rows:
    c = r['message_content']
    if not isinstance(c, bytes):
        continue
    if c[:4] != b'\x28\xb5\x2f\xfd':
        continue
    count += 1
    if count > 8:
        break
    res = try_offsets(c)
    out.write(b'==== local_id=%s LEN=%d\n' % (str(r['local_id']).encode(), len(c)))
    for off, dec in res:
        out.write(b'  off=%d -> %d bytes: %r\n' % (off, len(dec), dec[:120]))
    if not res:
        out.write(b'  no zstd at any offset 0..15\n')
    out.write(b'\n')
