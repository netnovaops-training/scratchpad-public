import gzip, json, sys, re
s = json.load(gzip.open(sys.argv[1], 'rt', encoding='utf-8'))
for p in s['pages']:
    for m in re.finditer('password', p['content'], re.I):
        print(p['path'], repr(p['content'][max(0,m.start()-120):m.end()+80]))
