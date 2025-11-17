#!/bin/bash
set -euo pipefail
if [ $# -eq 0 ]; then
  lxc exec IntelliBot -- runuser -- bash
else
  cmd="$*"
  lxc exec IntelliBot -- runuser -- bash -lc "cd /workspace/IntelliBot && $cmd"
fi
