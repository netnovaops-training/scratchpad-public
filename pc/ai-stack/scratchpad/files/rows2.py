import re, collections, json
sql=open(r"C:\Users\USER\AppData\Local\Temp\claude\C--Jasons-New-Site-www-ai-stack\08938579-6dcf-4698-abca-ba2836a833f5\scratchpad\db.sql",encoding="utf-8").read()
cols={}
for t,body in re.findall(r'CREATE TABLE `([^`]+)` \((.*?)\n\)', sql, re.S):
    cols[t]=re.findall(r'^\s+`([^`]+)`', body, re.M)
def parse_values(s,i):
    rows=[];row=[];cur=[];inq=False;depth=0
    while i<len(s):
        c=s[i]
        if inq:
            if c==chr(92): cur.append(s[i:i+2]); i+=2; continue
            if c=="'": inq=False
            cur.append(c)
        else:
            if c=="'": inq=True; cur.append(c)
            elif c=='(' and depth==0: depth=1; row=[]; cur=[]
            elif c==',' and depth==1: row.append(''.join(cur)); cur=[]
            elif c==')' and depth==1: row.append(''.join(cur)); rows.append(row); depth=0
            elif c==';' and depth==0: return rows,i
            else: cur.append(c)
        i+=1
    return rows,i
data=collections.defaultdict(list)
for m in re.finditer(r'INSERT INTO `([^`]+)` VALUESs*', sql):
    rows,_=parse_values(sql,m.end())
    data[m.group(1)].extend(rows)
import pickle
pickle.dump((cols,dict(data)),open(r"C:\Users\USER\AppData\Local\Temp\claude\C--Jasons-New-Site-www-ai-stack\08938579-6dcf-4698-abca-ba2836a833f5\scratchpad\db.pkl","wb"))
for t in cols: print(t, len(data.get(t,[])))
