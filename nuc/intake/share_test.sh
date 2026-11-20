#!/bin/sh
# share_test.sh - sandbox test of the /share door (T0872). Runs in a curl container on nuc-homelab_default
# against intake-sbx (scratch folders, never the real libraries). Identity header is set directly
# because the sandbox is not behind Caddy; in production Caddy strips any inbound X-Auth-Email first.
set -u
B=http://intake-sbx:8100
H="X-Auth-Email: user@example.com"
cd /tmp
echo "== manifest"; curl -s $B/manifest.webmanifest | head -c 160; echo
echo "== page has manifest link"; curl -s -H "$H" $B/ | grep -c manifest.webmanifest
printf 'Electric bill\nAccount 0000\nAmount due: 42.10\nDue date: 2026-12-01\n' > bill.txt
printf 'Grocery receipt\nMilk 3.49\nBread 2.99\nTotal 6.48\n' > receipt.txt
mkdir -p z && cp bill.txt receipt.txt z/ && (cd z && zip -q ../two-docs.zip bill.txt receipt.txt) 2>/dev/null \
  || python3 -c "import zipfile;z=zipfile.ZipFile('two-docs.zip','w');z.write('bill.txt');z.write('receipt.txt');z.close()"
echo "== zip of 2 docs"; curl -s -H "$H" -H "Accept: application/json" -F "files=@two-docs.zip" $B/share; echo
echo "== public link"; curl -s -H "$H" -H "Accept: application/json" -F "title=Example page" -F "url=https://example.com/" $B/share; echo
echo "== private link refused"; curl -s -H "$H" -H "Accept: application/json" -F "url=http://paperless-ngx:8000/" $B/share; echo
echo "== text only"; curl -s -H "$H" -H "Accept: application/json" -F "title=Gemini answer" -F "text=Pi is about 3.14159. It is irrational." $B/share; echo
echo "== no identity"; curl -s -o /dev/null -w "%{http_code}\n" -F "text=x" $B/share
