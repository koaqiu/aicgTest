FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    WEB_PORT=8000 \
    WEB_PUBLIC=1

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libxcb1 \
        libgl1 \
        libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

COPY web_requirements.txt /app/web_requirements.txt
RUN python -m pip install --upgrade pip && \
    python -m pip install -r /app/web_requirements.txt

COPY . /app

EXPOSE 8000

CMD ["python", "main.py", "web","--public"]
