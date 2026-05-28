FROM python:3.11-slim

ARG APT_MIRROR=http://deb.debian.org/debian
ARG APT_SECURITY_MIRROR=http://security.debian.org/debian-security
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    WEB_PORT=8000 \
    WEB_PUBLIC=1

WORKDIR /app

RUN set -eux; \
    find /etc/apt -type f \( -name "*.list" -o -name "*.sources" \) -exec \
        sed -ri "s|https?://deb.debian.org/debian|${APT_MIRROR}|g; s|https?://security.debian.org/debian-security|${APT_SECURITY_MIRROR}|g" {} +; \
    printf 'Acquire::Retries "5";\nAcquire::http::Timeout "20";\nAcquire::https::Timeout "20";\n' > /etc/apt/apt.conf.d/80-network-retries; \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        libxcb1 \
        libgl1 \
        libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

COPY web_requirements.txt /app/web_requirements.txt
RUN set -eux; \
    python -m pip install --upgrade pip --retries 5 --timeout 120 -i "${PIP_INDEX_URL}" && \
    if [ -n "${PIP_TRUSTED_HOST}" ]; then \
        python -m pip install -r /app/web_requirements.txt --retries 5 --timeout 120 -i "${PIP_INDEX_URL}" --trusted-host "${PIP_TRUSTED_HOST}"; \
    else \
        python -m pip install -r /app/web_requirements.txt --retries 5 --timeout 120 -i "${PIP_INDEX_URL}"; \
    fi

COPY main.py /app/main.py
COPY src /app/src
COPY templates /app/templates
COPY web_static /app/web_static

EXPOSE 8000

CMD ["python", "main.py", "web","--public"]
