import collections
import pickle
import re
from pathlib import Path

P=r"C:\Users\USER\AppData\Local\Temp\claude\C--Jasons-New-Site-www-ai-stack\08938579-6dcf-4698-abca-ba2836a833f5\scratchpad"
# Unpickles a file this same scratch session wrote a minute earlier, on this PC; never input from anyone else.
cols,data=pickle.loads(Path(P+r"\db.pkl").read_bytes())  # nosec B301
sql=Path(P+r"\db.sql").read_text(encoding="utf-8")
pats={
 'email':r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
 'ipv4':r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
 'phone':r'\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b',
 'argon2':r'\$argon2',
 'bcrypt':r'\$2[aby]\$\d\d\$',
 'sk-':r'\bsk-[A-Za-z0-9_-]{16,}',
 'AIza':r'AIza[0-9A-Za-z_-]{30,}',
 'ghp/glpat':r'\b(?:ghp_|gho_|glpat-|xox[bp]-)[A-Za-z0-9_-]{10,}',
 'jwt':r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.',
 'hex64':r'\b[0-9a-f]{64}\b',
 'hex32':r'\b[0-9a-f]{32}\b',
 'password=':r'(?i)(password|passwd|api[_-]?key|secret|token)\s*[=:]\s*\S{6,}',
 'latlon':r'-?\d{1,2}\.\d{4,},\s*-?\d{2,3}\.\d{4,}',
}
for t,rows in data.items():
    c=cols.get(t,[])
    hits=collections.Counter(); where=collections.defaultdict(set); samples={}
    for r in rows:
        for i,v in enumerate(r):
            for k,p in pats.items():
                for mm in re.findall(p,v):
                    hits[k]+=1; col=c[i] if i<len(c) else i; where[k].add(col)
                    s = mm if isinstance(mm,str) else mm[0]
                    samples.setdefault((k,col),set()).add(s[:4])
    if hits: print(t, dict(hits), {k:sorted(map(str,v)) for k,v in where.items()})
    if hits:
        for (k,col),s in samples.items():
            if k in('email','ipv4','phone'): print('   ',k,col,'distinct',len(s),'prefixes',sorted(s)[:8])
# distinct usernames in user tables
for t in ['user_favorites','user_likes','user_done','user_prefs','garden_blog','plant_quantity_log','plant_purchase','garden_photo','plant_checkup','plant_tag_photo','video_source','vocab_alias','vocab_proposal','wiki_draft']:
    if t not in data: continue
    c=cols[t]
    for col in ['username','author','recorded_by','added_by','uploaded_by','asked_by','taken_by','approved_by','decided_by','reviewed_by']:
        if col in c:
            i=c.index(col); vals=collections.Counter(r[i].strip("'")[:6] for r in data[t])
            print(t,col,dict(vals))
