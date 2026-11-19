import re
import sys


def strip(p):
    return [l for l in open(p,encoding='utf8',newline='').read().replace('\r\n','\n').split('\n') if not re.match(r'^\s*//',l)]
a=strip(sys.argv[1]);b=strip(sys.argv[2])
print('OK' if a==b else 'DIFF')
with open(sys.argv[2],encoding='utf8') as fh:
    for l in fh:
        if len(l.rstrip('\n\r'))>100 and re.match(r'^\s*//',l):print('LONG',l)
