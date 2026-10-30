#!/bin/bash
# Mac runner: runs job scripts that appear in ./jobs (one at a time), natively on this Mac.
# Output of each job -> ./done/<job>.log ; heartbeat -> runner_status.txt. Close this window to stop it.
cd "$(dirname "$0")"
mkdir -p jobs done
echo "runner started $(date) on $(sysctl -n machdep.cpu.brand_string)"
while true; do
  echo "idle $(date +%H:%M:%S)" > runner_status.txt
  for j in jobs/*.sh; do
    [ -e "$j" ] || continue
    name=$(basename "$j" .sh); mv "$j" "done/$name.sh"
    echo "running $name since $(date +%H:%M:%S)" > runner_status.txt
    echo "$(date +%H:%M:%S) running job $name"
    caffeinate -i bash "done/$name.sh" > "done/$name.log" 2>&1; rc=$?
    echo "$(date +%H:%M:%S) job $name finished (exit $rc)"; echo "exit $rc" >> "done/$name.log"
  done
  sleep 5
done
