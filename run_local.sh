#!/usr/bin/env bash
# Checkpointed local runner; deletes data only after a verified final release.
cd "$(dirname "$0")"
exec python local.py "$@"
