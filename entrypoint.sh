#!/bin/sh
set -e

# first run: seed the mounted volume with editable configs
mkdir -p /data/briefs /data/study/topics /data/logs
[ -f /data/config.yaml ]  || { cp config.example.yaml  /data/config.yaml;  echo "seeded /data/config.yaml  — edit it for your search"; }
[ -f /data/profile.yaml ] || { cp profile.example.yaml /data/profile.yaml; echo "seeded /data/profile.yaml — put YOUR skills/resume facts in it"; }

# optional background refresh loop (the dashboard also auto-refreshes on open)
if [ "${JOBSCOUT_REFRESH_EVERY_HOURS:-0}" -gt 0 ] 2>/dev/null; then
    (
        while true; do
            sleep $((JOBSCOUT_REFRESH_EVERY_HOURS * 3600))
            echo "── scheduled refresh $(date) ──" >> /data/logs/refresh.log
            python run.py >> /data/logs/refresh.log 2>&1 || true
        done
    ) &
    echo "background refresh every ${JOBSCOUT_REFRESH_EVERY_HOURS}h enabled"
fi

exec python -m streamlit run dashboard.py \
    --server.port "${PORT:-8501}" --server.address 0.0.0.0 \
    --server.headless true --browser.gatherUsageStats false
