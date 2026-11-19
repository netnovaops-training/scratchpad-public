import re
import sys
from pathlib import Path

spec=Path(sys.argv[1]).read_bytes().decode('utf8').replace('\r','').split('\n')
src=Path(sys.argv[2]).read_bytes().decode('utf8')
crlf='\r\n' in src
lines=src.replace('\r\n','\n').split('\n')
blocks=[];cur=None
for l in spec:
    m=re.match(r'^@(\d+) (///|//!|//)$',l)
    if m:
        cur=[int(m.group(1)),m.group(2),[]];blocks.append(cur)
    elif cur is not None:
        cur[2].append(l)
ins={}
for n,p,t in blocks:
    while t and t[-1]=='':t.pop()
    tgt=lines[n-1]
    ind=re.match(r'\s*',tgt).group(0) if tgt.strip() else ''
    out=[]
    for x in t:
        s=(ind+p+' '+x) if x else (ind+p)
        assert len(s)<=100,(len(s),s)
        out.append(s)
    ins.setdefault(n,[]).extend(out)
res=[]
for i,l in enumerate(lines,1):
    res.extend(ins.get(i,[]));res.append(l)
Path(sys.argv[2]).write_text(('\r\n' if crlf else '\n').join(res), encoding='utf8', newline='')
