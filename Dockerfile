FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# The image carries no .git, so the version git reports is captured here.
# Build with:  docker build --build-arg APP_VERSION="$(git describe --tags --always --dirty)" .
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# image.source is what makes GHCR attach this package to the repo page and
# inherit its README; the other two just mirror APP_VERSION into metadata so
# `docker inspect` answers the same question the footer does.
LABEL org.opencontainers.image.source="https://github.com/salehihassen/quotes-store" \
      org.opencontainers.image.description="Quotes store -- FastAPI + htmx, backed by Postgres" \
      org.opencontainers.image.version="${APP_VERSION}"

RUN useradd --system --uid 1001 quotes
USER quotes

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
