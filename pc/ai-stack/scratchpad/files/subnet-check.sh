#!/bin/bash
# Compare the old and the new way of finding the home network.
old=$(ip -4 route 2>/dev/null | awk '!/default/ && /src/ {print $1; exit}')
dev=$(ip -4 route show default 2>/dev/null | awk '{print $5; exit}')
new=$(ip -4 route show dev "$dev" 2>/dev/null | awk '!/default/ && /src/ {print $1; exit}')
echo "all networks with an address:"; ip -4 route | awk '/src/ {print "   " $1, "on", $3}'
echo "old way picked: $old"
echo "new way picks:  $new  (the connection to the router: $dev)"
