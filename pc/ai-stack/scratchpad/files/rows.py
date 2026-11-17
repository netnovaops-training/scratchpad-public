import re, collections
sql=open(r"C:\Users\USER\AppData\Local\Temp\claude\C--Jasons-New-Site-www-ai-stack\08938579-6dcf-4698-abca-ba2836a833f5\scratchpad\db.sql",encoding="utf-8").read()
# split into statements roughly by lines starting with INSERT INTO
cnt=collections.Counter(); lines=collections.Counter()
for line in sql.split('\n'):
    m=re.match(r'INSERT INTO `([^`]+)`',line)
    if m:
        t=m.group(1); lines[t]+=1
        # count tuples: occurrences of "),(" plus 1 (approx)
        cnt[t]+=line.count('),(')+1
for t in sorted(cnt): print(t, cnt[t])
print("headers:", [l for l in sql.split('\n')[:30] if l.startswith('--')][:10])
