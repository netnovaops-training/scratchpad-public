#!/bin/bash
# Test-only: stop the wiki page import and wait for the install to finish.
sudo docker ps --format '{{.Names}}' | grep wiki-setup | xargs -r sudo docker kill >/dev/null
for i in $(seq 1 100); do grep -q '^exit=' /tmp/final-install.log && break; sleep 3; done
sed 's/\x1b\[[0-9;]*m//g' /tmp/final-install.log | grep -E 'STEP 1[34]|STOPPED|WARNING|ALL DONE|exit=|pages added' | tail -8
