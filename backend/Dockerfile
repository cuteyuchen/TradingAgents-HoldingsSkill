FROM python:3.12-slim

WORKDIR /app

# System deps for some finance packages.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data volume for the SQLite file.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

ENV ADVISOR_HOST=0.0.0.0
ENV ADVISOR_PORT=8000
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
