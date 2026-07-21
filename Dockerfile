FROM node:20-alpine AS frontend-build

WORKDIR /frontend

COPY frontend/package.json ./package.json
RUN npm install --no-audit --no-fund

COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim

WORKDIR /app

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

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
