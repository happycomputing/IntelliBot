#!/bin/bash
set -euo pipefail
if [ $# -eq 0 ]; then
  lxc exec IntelliBot -- runuser -u pieter -- bash
else
  cmd="$*"
  lxc exec IntelliBot -- runuser -u pieter -- bash -lc "cd /workspace/IntelliBot && $cmd"
fi
