FROM node:20-alpine AS frontend-build

WORKDIR /frontend

COPY frontend/package.json ./package.json
RUN npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim

WORKDIR /app

ARG APP_VERSION=0.3.0
ARG APP_GIT_SHA=UNKNOWN
ARG APP_BUILD_TIME=unknown

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./
COPY skill/ ./skill/
COPY --from=frontend-build /frontend/dist ./static/

RUN mkdir -p /app/data
VOLUME ["/app/data"]

ENV ADVISOR_HOST=0.0.0.0
ENV ADVISOR_PORT=8000
ENV ADVISOR_STATIC_DIR=/app/static
ENV HOLDINGS_SKILL_DIR=/app/skill/tradingagents-holdings-advisor
ENV APP_VERSION=${APP_VERSION}
ENV APP_GIT_SHA=${APP_GIT_SHA}
ENV APP_BUILD_TIME=${APP_BUILD_TIME}
ENV ADVISOR_BACKUP_DIR=/app/data/backups

EXPOSE 8000

CMD ["sh", "-c", "python -m app.system.startup && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
