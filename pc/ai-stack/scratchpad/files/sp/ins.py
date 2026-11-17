import sys, re, textwrap
# usage: ins.py SRC SPEC
src, spec = sys.argv[1], sys.argv[2]
lines = open(src, encoding='utf-8', newline='').read().split('\n')
crlf = any(l.endswith('\r') for l in lines[:5])
orig = [l.rstrip('\r') for l in lines]
items = []
cur = None
for raw in open(spec, encoding='utf-8').read().split('\n'):
    raw = raw.rstrip('\r')
    m = re.match(r'^@(\d+) ([dch]) ~~ ?(.*)$', raw)
    if m:
        cur = [int(m.group(1)), m.group(2), m.group(3), []]
        items.append(cur)
    elif cur is not None and raw.strip() != '':
        cur[3].append(raw)
    elif cur is not None and raw.strip() == '':
        pass
ALLOWED = ('pub', 'fn', 'async', 'struct', 'enum', 'const', 'static', 'impl', '#[', 'mod', 'use', 'type')
out = {}
errs = []
for ln, kind, exp, texts in items:
    line = orig[ln-1]
    if exp not in line:
        errs.append(f'MISMATCH {ln}: expected {exp!r} in {line!r}'); continue
    indent = re.match(r'\s*', line).group(0)
    if kind == 'h':
        indent = ''; pre = '//!'
    elif kind == 'd':
        pre = '///'
        if not re.match(r'^\s*[A-Z]', line) and not line.lstrip().startswith(ALLOWED) and not line.lstrip().startswith('///'):
            errs.append(f'BAD doc anchor {ln}: {line!r}'); continue
    else:
        pre = '//'
    new = []
    for t in texts:
        if t.strip() == '~':
            new.append(f'{indent}{pre}'); continue
        w = 100 - len(indent) - len(pre) - 1
        for piece in textwrap.wrap(t, w, break_long_words=False, break_on_hyphens=False):
            new.append(f'{indent}{pre} {piece}')
    out.setdefault(ln, []).extend(new)
if errs:
    print(chr(10).join(errs)); sys.exit(1)
res = []
for i, l in enumerate(orig, 1):
    if i in out: res.extend(out[i])
    res.append(l)
add = sum(len(v) for v in out.values())
nl = '\r\n' if crlf else '\n'
open(src, 'w', encoding='utf-8', newline='').write(nl.join(res))
bad = [l for l in res if len(l) > 100 and l.lstrip().startswith('//') and l not in orig]
print(src.split('/')[-1], 'added', add, 'toolong', len(bad))
