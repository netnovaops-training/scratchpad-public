import sys,re
def strip(p):
    return [l for l in open(p,encoding='utf8').read().replace('\r\n','\n').split('\n') if not re.match(r'^\s*//',l)]
a=strip(sys.argv[1]);b=strip(sys.argv[2])
print('OK' if a==b else 'DIFF')
for l in open(sys.argv[2],encoding='utf8'):
    if len(l.rstrip('\n\r'))>100 and re.match(r'^\s*//',l):print('LONG',l)
