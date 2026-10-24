#!/usr/bin/env bash
# Runs one local-only source to completion, publishes it to the private data repo's releases, then removes the
# local copy.   ./run_local.sh audit
src=$1
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8 DATA_REPO=mashbat1/gov-crawler
for i in 1 2 3 4 5; do
  python run.py "$src" --budget-min 1400 --workdir "work_$src" >> "logs_$src.txt" 2>&1
  python -c "import json,sys; sys.exit(0 if json.load(open('work_$src/result.json'))['complete'] else 1)" && break
  echo "$(date) run $i incomplete, retrying" >> "logs_$src.txt"
done
WORKDIR="work_$src" python ci.py save "$src" >> "logs_$src.txt" 2>&1 && rm -rf "work_$src" && echo "$(date) $src published and local copy removed" >> "logs_$src.txt"
