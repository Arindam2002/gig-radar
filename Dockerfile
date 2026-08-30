FROM python:3.13-slim

# node is only needed for the Firecrawl CLI (Naukri source + contact search);
# everything else is pure python
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g firecrawl-cli \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt

COPY jobscout/ jobscout/
COPY .streamlit/ .streamlit/
COPY run.py brief.py dashboard.py entrypoint.sh \
     config.example.yaml profile.example.yaml ./
RUN chmod +x entrypoint.sh

# all user-owned state lives in one mounted volume
ENV JOBSCOUT_CONFIG=/data \
    JOBSCOUT_DB=/data/jobscout.db \
    JOBSCOUT_BRIEFS=/data/briefs \
    JOBSCOUT_STUDY=/data/study \
    JOBSCOUT_LOGS=/data/logs \
    JOBSCOUT_FIRECRAWL_CMD=firecrawl \
    JOBSCOUT_REFRESH_EVERY_HOURS=0
VOLUME /data
EXPOSE 8501

ENTRYPOINT ["./entrypoint.sh"]
