# PubMigrator on Hugging Face Spaces (free hosting)
# HF Docker Spaces expect the app on port 7860.

FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-draw \
        poppler-utils \
        fonts-liberation \
        fonts-dejavu-core \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && libreoffice --headless --version

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py config.py converter.py verifier.py ./
COPY templates/ templates/

ENV PORT=7860
EXPOSE 7860

# 2 workers => max 2 concurrent conversions, each ~200-400MB RAM.
# HF free CPU tier: 2 vCPU / 16GB RAM — plenty of headroom.
CMD ["gunicorn", "--bind", "0.0.0.0:7860", "--workers", "2", \
     "--threads", "4", "--timeout", "180", "app:app"]
