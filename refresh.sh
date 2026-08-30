#!/bin/bash
# Fetch fresh listings and log the run. Schedule with cron/launchd (see README).
cd "$(dirname "$0")"
mkdir -p logs
echo "── refresh $(date '+%Y-%m-%d %H:%M') ──" >> logs/refresh.log
.venv/bin/python run.py >> logs/refresh.log 2>&1
echo "" >> logs/refresh.log
