ps -eo pid,ppid,etime,stat,args --sort=start_time | grep -E 'garden|docker compose|sleep|curl|read' | grep -v grep | head -20
pkill -f 'garden.sh redo-renames' && echo "stale redo stopped"
sleep 2
ps -eo pid,ppid,etime,stat,args | grep -E 'garden.sh|docker compose' | grep -v grep
docker ps -a --format '{{.Names}} {{.Status}}'
