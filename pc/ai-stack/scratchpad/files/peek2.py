import gzip
import json
import re
import sys

with gzip.open(sys.argv[1], 'rt', encoding='utf-8') as fh:
    s = json.load(fh)
for p in s['pages']:
    for m in re.finditer('password', p['content'], re.IGNORECASE):
        print(p['path'], repr(p['content'][max(0,m.start()-120):m.end()+80]))
