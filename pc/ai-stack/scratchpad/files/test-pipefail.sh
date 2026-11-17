#!/bin/bash
# Does "long output | grep -q" give a wrong "no" under pipefail? (garden.sh uses set -uo pipefail)
set -uo pipefail
first=$(systemctl list-unit-files 2>/dev/null | head -3 | tail -1 | awk '{print $1}')
echo "looking for an early line: $first (of $(systemctl list-unit-files 2>/dev/null | wc -l) lines)"
bad=0; for _ in $(seq 50); do systemctl list-unit-files 2>/dev/null | grep -q "^$first" || bad=$((bad + 1)); done
echo "grep -q            : wrong 'no' $bad of 50 times"
bad=0; for _ in $(seq 50); do systemctl list-unit-files 2>/dev/null | grep "^$first" >/dev/null || bad=$((bad + 1)); done
echo "grep ... >/dev/null: wrong 'no' $bad of 50 times"
bad=0; for _ in $(seq 50); do seq 1 200000 | grep -q '^5$' || bad=$((bad + 1)); done
echo "very long output, grep -q: wrong 'no' $bad of 50 times"
